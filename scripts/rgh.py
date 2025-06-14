#! /usr/bin/env python3
from collections import OrderedDict
from colorama import Fore, Style
import argparse
import colorama
import functools
import itertools
import json
import os
import os.path
import random
import re
import shutil
import string
import subprocess
import sys
import tempfile
import time

DRY_RUN = False
ASK = True
TOKEN = None

def error(message):
    print(f'{Fore.RED}{Style.BRIGHT}error:{Style.RESET_ALL} {message}', file=sys.stderr)
    sys.exit(1)

def ask(message):
    if not ASK:
        return
    while True:
        answer = input(f'{Fore.YELLOW}{Style.BRIGHT}{message}{Style.RESET_ALL} (y/n) ')
        if answer in ['n', 'no']:
            print('Aborted by user.')
            sys.exit(1)
        if answer in ['y', 'yes']:
            return
        print("Please enter 'y' or 'n'")

def print_cmd(cmd):
    pretty = ' '.join(['"'+c+'"' if ' ' in c else c for c in map(str, cmd)])
    print(f'{Fore.YELLOW}{pretty}{Style.RESET_ALL}')

# runs `cmd` (must be a list)
# prints command output to stdout
# dies if command fails
# if `retry_fn` is given, retries failed command while retry_fn(cmd_output) returns True
def run_cmd(cmd, input=None, env=None, retry_fn=None):
    cmd = [str(c) for c in cmd]

    environ = os.environ.copy()
    if TOKEN:
        environ['GH_TOKEN'] = TOKEN
    if env:
        environ.update(env)

    if input:
        input = input.encode()

    stdout = None
    if retry_fn:
        stdout = subprocess.PIPE

    max_tries = 6

    while True:
        try:
            print_cmd(cmd)
            if DRY_RUN:
                return
            proc = subprocess.run(cmd, input=input, stdout=stdout, env=environ, check=True)
            if stdout is not None:
                output = proc.stdout.decode()
                print(output, end='')
        except subprocess.CalledProcessError as e:
            output = ''
            if e.output:
                output = e.output.decode()
            if retry_fn is not None and retry_fn(output) and max_tries > 0:
                print('Retrying...')
                max_tries -= 1
                time.sleep(1)
                continue
            error('command failed')
        return

# extract (org, repo) from --repo=org/repo
def parse_repo(s):
    org, repo = None, None

    if s:
        if '/' in s:
            org, repo = s.split('/', 2)
        else:
            org = 'roc-streaming'
            repo = s

    if repo is None:
        try:
            def_repo = subprocess.check_output(
                ['gh', 'repo', 'set-default', '--view'], text=True).strip()
            if '/' in def_repo:
                org, repo = def_repo.split('/', 2)
        except subprocess.CalledProcessError:
            pass

    if repo is None:
        url = subprocess.check_output(
            ['git', 'config', '--get', 'remote.origin.url'], text=True).strip()
        m = re.search(r'[:/](.+)/(.+?)(.git)?$', url)
        org, repo = m.group(1), m.group(2)

    return org, repo

# check availability of required tools
def check_tools():
    if not shutil.which('git'):
        error("'git' not found in PATH")

    if not shutil.which('gh'):
        error("'gh' not found in PATH")

    try:
        subprocess.check_call(['gh', 'auth', 'status'], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        error("'gh' not logged in, run 'gh auth login'")

# create worktree with unique name and path and chdir to it
def enter_worktree():
    def random_dir():
        while True:
            path = '/tmp/rgh-' + ''.join(random.choice(string.ascii_lowercase + string.digits)
                for _ in range(8))
            if not os.path.exists(path):
                return path

    old_path = os.path.abspath(os.getcwd())
    new_path = random_dir()

    run_cmd([
        'git', 'worktree', 'add', '--no-checkout', new_path
        ])

    print_cmd(['cd', new_path])
    if not DRY_RUN:
        os.chdir(new_path)

    return old_path

# remove worktree and chdir back to repo
def leave_worktree(old_path):
    new_path = os.path.abspath(os.getcwd())

    print_cmd(['cd', old_path])
    os.chdir(old_path)

    run_cmd([
        'git', 'worktree', 'remove', '-f', os.path.basename(new_path)
        ])

# return current head
def remember_ref():
    if DRY_RUN:
        return 'none'
    try:
        output = subprocess.run(
            ['git', 'symbolic-ref', '--short', 'HEAD'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout
    except:
        output = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout
    return output.decode().strip()

# restore remembered head
def restore_ref(ref):
    if os.path.exists('.git/index.lock'):
        run_cmd(['git', 'rebase', '--abort'])

    run_cmd(['git', 'checkout', ref])

# delete remembered head
def delete_ref(ref):
    run_cmd(['git', 'branch', '-D', ref])

# detect issue number from PR text
def guess_issue(org, repo, text):
    if not text:
        return None

    delim = r'[,:;?!()\[\]|+*_~<> \t\n\r]'

    prefix = f'(?:^|(?<={delim}))'
    suffix = f'(?:$|(?={delim}))'

    patterns = [
        r'(?:#|gh-)(\d+)',
        r'([\w-]+)/([\w-]+)#(\d+)',
        r'https?://github.com/([\w-]+)/([\w-]+)/issues/(\d+)(?:#[\w\d-]+)?',
    ]

    regexp = re.compile('|'.join([prefix + p + suffix for p in patterns]),
        re.IGNORECASE | re.M)

    for m in regexp.finditer(text):
        if m.group(1):
            return org, repo, int(m.group(1))

        if m.group(2) == org:
            return m.group(2), m.group(3), int(m.group(4))

        if m.group(5) == org:
            return m.group(5), m.group(6), int(m.group(7))

    return None

# construct regexp for matching prefix or suffix of PR or commit title
# with issue number
# returned regexp must be suitable for both python and sed
# see make_message() and reword_pr_commits()
def make_prefix_suffix_regexp(org, repo, is_prefix):
    kv_rx = "fix|fixes|issue|ticket|task"
    nm_rx = "|".join([
        r"gh-[0-9]+",
        r"#?[0-9]+",
        f"{org}/[^ ]+",
    ])

    ref_rx = f"({kv_rx}\\s+)?({nm_rx})"
    sep_rx = r"[][(){}:—–-]"

    rx = f"\\s*{sep_rx}?\\s*({ref_rx})\\s*{sep_rx}?\\s*"

    if is_prefix:
        return f"^({rx})?"
    else:
        return f"(({rx})?\\.?\\s*$)"

def make_prefix_regexp(org, repo):
    return make_prefix_suffix_regexp(org, repo, is_prefix=True)

def make_suffix_regexp(org, repo):
    return make_prefix_suffix_regexp(org, repo, is_prefix=False)

# format prefix for commit message
def make_prefix(org, repo, issue_link):
    if not issue_link:
        error("can't determine issue associated with pr\n"
              "add issue number to pr description or use --issue or --no-issue")

    issue_org, issue_repo, issue_number = issue_link

    if issue_org == org and issue_repo == repo:
        return f'gh-{issue_number}'
    else:
        return f'{issue_org}/{issue_repo}#{issue_number}'

# format commit message
def make_message(org, repo, issue_link, pr_title):
    pr_title = re.sub(make_prefix_regexp(org, repo), '', pr_title, flags=re.IGNORECASE)
    pr_title = re.sub(make_suffix_regexp(org, repo), '', pr_title, flags=re.IGNORECASE)

    if issue_link:
        return '{}: {}'.format(
            make_prefix(org, repo, issue_link),
            pr_title)
    else:
        return pr_title

@functools.cache
def query_issue_info(org, repo, issue_number):
    issue_info = {}

    try:
        response = json.loads(subprocess.run([
            'gh', 'api',
            f'/repos/{org}/{repo}/issues/{issue_number}'
            ],
            capture_output=True, text=True, check=True).stdout)
    except subprocess.CalledProcessError as e:
        error(f'failed to retrieve issue info: {e.stderr.strip()}')

    issue_info['issue_title'] = response['title']
    issue_info['issue_url'] = response['html_url']
    issue_info['issue_author'] = response['user']['login']

    if response['milestone']:
        issue_info['issue_milestone'] = response['milestone']['title']
    else:
        issue_info['issue_milestone'] = None

    issue_info['issue_labels'] = list(sorted(
        [label['name'] for label in response['labels']]))

    return issue_info

@functools.cache
def query_pr_info(org, repo, pr_number, no_git=False):
    try:
        response = json.loads(subprocess.run(
            ['gh', 'api', f'/repos/{org}/{repo}/pulls/{pr_number}'],
            capture_output=True, text=True, check=True).stdout)
    except subprocess.CalledProcessError as e:
        error(f'failed to retrieve pr info: {e.stderr.strip()}')

    pr_info = {
        'pr_link': (org, repo, pr_number),
        'pr_title': response['title'],
        'pr_url': response['html_url'],
        'pr_author': response['user']['login'],
        'pr_state': response['state'],
        'pr_draft': response['draft'],
        'pr_mergeable': response['mergeable'],
        'pr_rebaseable': response['rebaseable'],
        # branch in pr author's repo
        'source_branch': response['head']['ref'],
        'source_sha': response['head']['sha'],
        'source_remote': response['head']['repo']['ssh_url'],
        # branch in upstream repo
        'target_branch': response['base']['ref'],
        'target_remote': response['base']['repo']['ssh_url'],
    }

    if not no_git:
        try:
            pr_info['target_sha'] = subprocess.run(
                ['git', 'ls-remote', pr_info['target_remote'], pr_info['target_branch']],
                capture_output=True, text=True, check=True).stdout.split()[0]
        except subprocess.CalledProcessError as e:
            error("can't determine target commit")

    if response['milestone']:
        pr_info['pr_milestone'] = response['milestone']['title']
    else:
        pr_info['pr_milestone'] = None

    pr_info['pr_labels'] = list(sorted(
        [label['name'] for label in response['labels']]))

    pr_info['issue_link'] = None

    if 'body' in response:
        pr_info['issue_link'] = pr_info['issue_link_in_body'] = \
          guess_issue(org, repo, response['body'])

    if not pr_info['issue_link'] and 'title' in response:
        pr_info['issue_link'] = guess_issue(org, repo, response['title'])

    if not pr_info['issue_link'] and 'issue' in response:
        pr_info['issue_link'] = (org, repo, int(response['issue']['number']))

    if pr_info['issue_link']:
        issue_info = query_issue_info(*pr_info['issue_link'])
        pr_info.update(issue_info)

    review_info = query_pr_review(org, repo, pr_number)
    pr_info.update(review_info)

    return pr_info

@functools.cache
def query_pr_review(org, repo, pr_number):
    try:
        response = subprocess.run(
            ['gh', 'pr', 'view',
             '--repo', f'{org}/{repo}',
             str(pr_number),
             '--json',
             'reviewRequests,reviews'],
            capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        error(f'failed to retrieve review state: {e.stderr.strip()}')

    data = json.loads(response.stdout)

    def _review_decision():
        reviews = data['reviews']

        # filter out non-MEMBERS
        reviews = [r for r in reviews if r.get('authorAssociation') == 'MEMBER']
        if not reviews:
            return 'none'

        # filter out COMMENTED reviews
        reviews = [r for r in reviews if r['state'] != 'COMMENTED']
        if not reviews:
            return 'commented'

        # keep only the latest review from each author
        unique_authors = []
        unique_states = []
        for review in reversed(reviews):
            if review['author']['login'] not in unique_authors:
                unique_authors.append(review['author']['login'])
                unique_states.append(review['state'])

        # consider pr approved only if last review of every MEMBER that leaved
        # non-COMMENTED review was APPROVED
        if all(state == 'APPROVED' for state in unique_states):
            return 'approved'
        else:
            return 'changes_requested'

    review_info = {}
    review_info['review_requested'] = len(data['reviewRequests']) != 0
    review_info['review_decision'] = _review_decision()

    return review_info

@functools.cache
def query_pr_actions(org, repo, pr_number, no_git=False, silent_error=False):
    pr_info = query_pr_info(org, repo, pr_number, no_git)

    try:
        response = json.loads(subprocess.run([
            'gh', 'pr', 'checks',
            '--repo', f'{org}/{repo}',
            '--json', 'workflow,state',
            str(pr_number),
            ],
            capture_output=True, text=True, check=True).stdout)
    except subprocess.CalledProcessError as e:
        if silent_error:
            response = []
        else:
            error(f'failed to retrieve workflow runs: {e.stderr.strip()}')

    results = {}
    for check in response:
        results[check['workflow']] = check['state'].lower()

    return sorted(results.items())

@functools.cache
def query_pr_commits(org, repo, pr_number, no_git=False):
    try:
        response = json.loads(subprocess.run([
            'gh', 'pr', 'view',
            '--repo', f'{org}/{repo}',
            '--json', 'commits',
            str(pr_number),
            ],
            capture_output=True, text=True, check=True).stdout)
    except subprocess.CalledProcessError as e:
        error(f'failed to retrieve pr commits: {e.stderr.strip()}')

    results = []
    for commit in response['commits']:
        results.append((commit['oid'],
                commit['messageHeadline'],
                commit['authors'][0]['name'], commit['authors'][0]['email']))

    return results

@functools.cache
def query_pr_author(org, repo, pr_number, no_git=False):
    pr_info = query_pr_info(org, repo, pr_number, no_git)

    author_info = {}

    try:
        subprocess.run(
            ['gh', 'api', f'/orgs/{org}/members/'+pr_info['pr_author']],
            capture_output=True, text=True, check=True)
        author_info['is_contrib'] = False
    except subprocess.CalledProcessError as e:
        author_info['is_contrib'] = True

    try:
       n_pullreqs = int(subprocess.check_output(
            ['gh', 'pr', 'list',
             '--repo', f'{org}/{repo}',
             '--author', pr_info['pr_author'],
             '--state', 'all',
             '--json', 'number',
             '--jq', 'length',
             ],
            text=True))
    except subprocess.CalledProcessError as e:
        pass

    author_info['is_first'] = n_pullreqs == 0

    return author_info

# find commit in target branch from which PR's branch was forked
@functools.cache
def find_pr_fork_point(org, repo, pr_number):
    pr_info = query_pr_info(org, repo, pr_number)

    try:
        # find oldest commit in current branch that is not present in target
        first_commit = subprocess.run(
            ['git', 'rev-list', '--first-parent', '^'+pr_info['target_sha'], pr_info['source_sha']],
            capture_output=True, text=True, check=True).stdout.split()[-1].strip()
        # find its parent
        fork_point = subprocess.run(
            ['git', 'rev-parse', first_commit+'^'],
            capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError as e:
        error("can't determine fork point")

    return fork_point

# dump pr info in json format
def build_pr_json(org, repo, pr_number):
    pr_info = query_pr_info(org, repo, pr_number, no_git=True)
    pr_actions = query_pr_actions(org, repo, pr_number, no_git=True, silent_error=True)
    pr_commits = query_pr_commits(org, repo, pr_number, no_git=True)
    pr_author = query_pr_author(org, repo, pr_number, no_git=True)

    result = OrderedDict()

    result['pull_request'] = OrderedDict([
        ('title', pr_info['pr_title']),
        ('url', pr_info['pr_url']),
        ('author', pr_info['pr_author']),
        ('milestone', pr_info['pr_milestone']),
        ('labels', pr_info['pr_labels']),
        ('source_branch', pr_info['source_branch']),
        ('target_branch', pr_info['target_branch']),
        ('state', pr_info['pr_state']),
        ('is_draft', pr_info['pr_draft']),
        ('is_mergeable', pr_info['pr_mergeable']),
        ('is_rebaseable', pr_info['pr_rebaseable']),
        ])

    if pr_info['issue_link']:
        result['linked_issue'] = OrderedDict([
            ('title', pr_info['issue_title']),
            ('url', pr_info['issue_url']),
            ('author', pr_info['issue_author']),
            ('milestone', pr_info['issue_milestone']),
            ('labels', pr_info['issue_labels']),
        ])
    else:
        result['linked_issue'] = None

    result['author'] = OrderedDict([
        ('is_contrib', pr_author['is_contrib']),
        ('is_first', pr_author['is_first']),
    ])

    result['review'] = OrderedDict([
        ('requested', pr_info['review_requested']),
        ('decision', pr_info['review_decision']),
    ])

    result['actions'] = OrderedDict()
    for action_name, action_result in pr_actions:
        has_actions = True
        result['actions'][action_name] = action_result

    result['commits'] = []
    for commit_sha, commit_msg, commit_author, commit_email in pr_commits:
        result['commits'].append(OrderedDict([
            ('message', commit_msg),
            ('sha', commit_sha),
            ('author', commit_author),
            ('email', commit_email),
        ]))

    return result

# dump issue info in json format
def build_issue_json(org, repo, issue_number):
    issue_info = query_issue_info(org, repo, issue_number)

    result = OrderedDict()

    result['issue'] = OrderedDict([
        ('title', issue_info['issue_title']),
        ('url', issue_info['issue_url']),
        ('author', issue_info['issue_author']),
        ('milestone', issue_info['issue_milestone']),
        ('labels', issue_info['issue_labels']),
    ])

    return result

def print_json(js):
    if sys.stdout.isatty():
        print(json.dumps(js, indent=2))
    else:
        print(json.dumps(js))

def print_text(text, color=None, depth=0):
    indent = ''
    if depth:
        indent = ('    ' * depth)
    if color:
        print(f'{indent}{color}{Style.BRIGHT}{text}{Style.RESET_ALL}')
    else:
        print(f'{indent}{text}')

def print_kv(key, val, color=None, depth=0):
    indent = ''
    if depth:
        indent = ('    ' * depth)
    if color:
        print(f'{indent}{key}: {color}{Style.BRIGHT}{val}{Style.RESET_ALL}')
    else:
        print(f'{indent}{key}: {val}')

def print_arr(key, vals, colors, depth):
    if vals:
        print_text(f'{key}:', color=None, depth=depth)
        for n, val in enumerate(vals):
            color = None
            if colors:
                color = colors[n]
            print_text(val, color, depth+1)
    else:
        print_text(f'{key}: none', color=None, depth=depth)

# print info about PR and linked issue
def show_pr(org, repo, pr_number, show_json):
    js = build_pr_json(org, repo, pr_number)

    if show_json:
        print_json(js)
        return

    print_text('pull request:', Fore.GREEN, depth=0)

    for k, v in js['pull_request'].items():
        if k == 'labels':
            colors = [Fore.YELLOW if label.startswith('S-') else None \
                      for label in v]
            print_arr('labels', v, colors, depth=1)
            continue

        color = None
        if k == 'title': color = Fore.BLUE
        elif k == 'milestone':
            if v: color = Fore.MAGENTA
            else:
                color = Fore.RED
                v = 'none'
        elif k == 'source_branch' or k == 'target_branch':
            color = Fore.CYAN
        elif k == 'state':
            if v == 'open': color = Fore.MAGENTA
            else: color = Fore.RED
        elif k == 'is_draft':
            if not v: color = Fore.MAGENTA
            else: color = Fore.RED
            v = str(v).lower()
        elif k == 'is_mergeable' or k == 'is_rebaseable':
            if v: color = Fore.MAGENTA
            else: color = Fore.RED
            v = str(v).lower()

        print_kv(k, v, color, depth=1)

    print_text('linked issue:', Fore.GREEN, depth=0)

    if js['linked_issue']:
        for k, v in js['linked_issue'].items():
            if k == 'labels':
                colors = [Fore.YELLOW if label.startswith('S-') else None \
                          for label in v]
                print_arr('labels', v, colors, depth=1)
                continue

            color = None
            if k == 'title': color = Fore.BLUE
            elif k == 'milestone':
                if v: color = Fore.MAGENTA
                else:
                    color = Fore.RED
                    v = 'none'
            print_kv(k, v, color, depth=1)
    else:
        print_text('none', Fore.RED, depth=1)

    print_text('author:', Fore.GREEN, depth=0)

    for k, v in js['author'].items():
        color = Fore.MAGENTA
        if v:
            color = Fore.YELLOW
        v = str(v).lower()
        print_kv(k, v, color, depth=1)

    print_text('review:', Fore.GREEN, depth=0)

    for k, v in js['review'].items():
        color = None
        if k == 'requested':
            if v: color = Fore.RED
            else: color = Fore.MAGENTA
            v = str(v).lower()
        elif k == 'decision':
            if v == 'changes_requested': color = Fore.RED
            else: color = Fore.MAGENTA
        print_kv(k, v, color, depth=1)

    print_text('actions:', Fore.GREEN, depth=0)

    if js['actions']:
        for k, v in js['actions'].items():
            color = None
            if v == 'success': color = Fore.MAGENTA
            else: color = Fore.RED
            print_kv(k, v, color, depth=1)
    else:
        print_text('none', Fore.RED, depth=1)

    print_text('commits:', Fore.GREEN, depth=0)

    if js['commits']:
        for commit in js['commits']:
            sha, msg, author, email = \
                commit['sha'], commit['message'], commit['author'], commit['email']
            if 'users.noreply.github.com' in email:
                email = 'noreply.github.com'
            print(f'    {sha[:8]} {Fore.BLUE}{Style.BRIGHT}{msg}{Style.RESET_ALL}'+
                f' ({author} <{email}>)')
    else:
        print_text('none', Fore.RED, depth=1)

# print info about issue
def show_issue(org, repo, issue_number, show_json):
    js = build_issue_json(org, repo, issue_number)

    if show_json:
        print_json(js)
        return

    print_text('issue:', Fore.GREEN, depth=0)

    for k, v in js['issue'].items():
        if k == 'labels':
            colors = [Fore.YELLOW if label.startswith('S-') else None \
                      for label in v]
            print_arr('labels', v, colors, depth=1)
            continue

        color = None
        if k == 'title': color = Fore.BLUE
        elif k == 'milestone':
            if v: color = Fore.MAGENTA
            else:
                color = Fore.RED
                v = 'none'
        print_kv(k, v, color, depth=1)

# die if PR does not fulfill all requirements
def verify_pr(org, repo, pr_number, issue_number, issue_miletsone, no_issue, no_milestone,
              ignore_actions, ignore_state, ignore_review):
    pr_info = query_pr_info(org, repo, pr_number)

    if not no_issue:
        if issue_number:
            issue_info = query_issue_info(org, repo, issue_number)
        else:
            issue_info = pr_info

        if not issue_number and not pr_info['issue_link']:
            error("can't determine issue associated with pr\n"
                  "add issue number to pr description or use --issue or --no-issue")

        if not no_milestone:
            if not issue_miletsone and not issue_info['issue_milestone']:
                error("can't determine milestone associated with issue\n"
                      "assign milestone to issue or use --milestone or --no-milestone")

    if not ignore_state:
        if pr_info['pr_state'] != 'open':
            error("can't proceed on non-open pr\n"
                  "use --ignore-state to proceed anyway")

        if pr_info['pr_draft']:
            error("can't proceed on draft pr\n"
                  "use --ignore-state to proceed anyway")

    if not ignore_actions:
        for action_name, action_result in query_pr_actions(org, repo, pr_number):
            if action_result != 'success':
                error("can't proceed on pr with failed checks\n"
                      "use --ignore-actions to proceed anyway")

    if not ignore_review:
        if pr_info['review_decision'] == 'changes_requested':
            error("can't proceed on pr with requested changes\n"
                  "use --ignore-review to proceed anyway")

# checkout PR's branch
def checkout_pr(org, repo, pr_number):
    pr_info = query_pr_info(org, repo, pr_number)

    local_branch = os.path.basename(os.getcwd())
    target_branch = pr_info['target_branch']

    run_cmd([
        'gh', 'pr', 'checkout',
        '--repo', f'{org}/{repo}',
        '-f',
        '-b', local_branch,
        pr_number,
        ])

# update PR meta-data on github
# (link issue to PR, set milestone of PR and issue, etc)
def update_pr_metadata(org, repo, pr_number, issue_number, issue_milestone,
                       no_issue, no_milestone):
    def _update_linked_issue():
        pr_info = query_pr_info(org, repo, pr_number)

        nonlocal issue_number
        if not issue_number:
            if pr_info['issue_link']:
                _, _, issue_number = pr_info['issue_link']

        is_uptodate = pr_info['issue_link'] and \
            (pr_info['issue_link'] == pr_info['issue_link_in_body']) and \
            (pr_info['issue_link'] == (org, repo, issue_number))

        if is_uptodate:
            return

        ask(f'Link to issue gh-{issue_number}?')

        try:
            response = json.loads(subprocess.run(
                ['gh', 'api', f'/repos/{org}/{repo}/pulls/{pr_number}'],
                capture_output=True, text=True, check=True).stdout)
        except subprocess.CalledProcessError as e:
            error(f'failed to retrieve pr info: {e.stderr.strip()}')

        body = '{}\n\n{}'.format(
            make_prefix(org, repo, (org, repo, issue_number)),
            (response['body'] or '').strip()).strip()

        run_cmd([
            'gh', 'pr', 'edit',
            '--repo', f'{org}/{repo}',
            '--body-file', '-',
            pr_number,
            ],
            input=body)

        query_pr_info.cache_clear()

    def _update_linked_milestone():
        pr_info = query_pr_info(org, repo, pr_number)

        issue_uptodate = pr_info['issue_milestone'] and \
            (not issue_milestone or pr_info['issue_milestone'] == issue_milestone)

        pr_uptodate = pr_info['pr_milestone'] and \
            pr_info['pr_milestone'] == pr_info['issue_milestone']

        if issue_uptodate and pr_uptodate:
            return

        ask(f'Link to milestone {issue_milestone}?')

        if not issue_uptodate:
            issue_org, issue_repo, issue_number = pr_info['issue_link']

            run_cmd([
                'gh', 'issue', 'edit',
                '--repo', f'{issue_org}/{issue_repo}',
                '--milestone', issue_milestone,
                issue_number,
                ])

        if not pr_uptodate:
            run_cmd([
                'gh', 'pr', 'edit',
                '--repo', f'{org}/{repo}',
                '--milestone', pr_info['issue_milestone'],
                pr_number,
                ])

        query_issue_info.cache_clear()
        query_pr_info.cache_clear()

    if not no_issue:
        _update_linked_issue()

        if not no_milestone:
            _update_linked_milestone()
            _update_milestone_of_pr()

# fetch source and target commits
def fetch_pr_commits(org, repo, pr_number):
    pr_info = query_pr_info(org, repo, pr_number)

    run_cmd([
        'git', 'fetch', pr_info['source_remote'], pr_info['source_sha']
        ])

    run_cmd([
        'git', 'fetch', pr_info['target_remote'], pr_info['target_sha']
        ])

# squash all commits in PR's local branch into one
# invoked before rebase
def squash_pr_commits(org, repo, pr_number, title, no_issue):
    pr_info = query_pr_info(org, repo, pr_number)

    # build and validate commit message
    commit_message = make_message(
        org, repo,
        pr_info['issue_link'] if not no_issue else None,
        title or pr_info['pr_title'])

    if len(commit_message) > 72:
        error("commit message too long, use --title to overwrite")

    # merge target into PR's branch
    run_cmd([
        'git', 'merge', '--no-edit', pr_info['target_sha'],
        ])

    # find where PR's branch forked from target branch
    fork_point = find_pr_fork_point(org, repo, pr_number)

    # squash all commits since fork point into one
    run_cmd([
        'git', 'reset', '--soft', fork_point,
        ])
    run_cmd([
        'git', 'commit', '-C', pr_info['source_sha'],
        ])

    # edit message
    run_cmd([
        'git', 'commit', '--amend', '--no-edit', '-m', commit_message,
        ])

# rebase PR's local branch on its target branch
def rebase_pr_commits(org, repo, pr_number):
    pr_info = query_pr_info(org, repo, pr_number)

    # find where PR's branch forked from target branch
    fork_point = find_pr_fork_point(org, repo, pr_number)

    # rebase commits from fork point to HEAD on target
    run_cmd([
        'git', 'rebase', '--onto', pr_info['target_sha'], fork_point
        ])

# add issue prefix to every commit in PR's local branch
# invoked after rebase
def reword_pr_commits(org, repo, pr_number, title, no_issue):
    pr_info = query_pr_info(org, repo, pr_number)

    if no_issue:
        commit_prefix = ''
    else:
        commit_prefix = make_prefix(org, repo, pr_info['issue_link']) + ': '

    target_sha = pr_info['target_sha']

    if title:
        title = title.replace(r',', r'\,')
        sed = f"sed -r"+\
            f" -e '1s,^.*$,{commit_prefix}{title},'"
    else:
        sed = f"sed -r"+\
          f" -e '1s,{make_prefix_regexp(org, repo)},{commit_prefix},I'"+\
          f" -e '1s,{make_suffix_regexp(org, repo)},,I'"

    run_cmd([
        'git', 'filter-branch', '-f', '--msg-filter', sed,
        f'{target_sha}..HEAD',
        ],
        env={'FILTER_BRANCH_SQUELCH_WARNING':'1'})

# print commits from local PR's branch
# invoked after rebase
def print_pr_commits(org, repo, pr_number):
    pr_info = query_pr_info(org, repo, pr_number)

    target_sha = pr_info['target_sha']

    run_cmd([
        'git', 'log', '--format=%h %s (%an <%ae>)',
        f'{target_sha}..HEAD',
        ])

# force-push PR's local branch to upstream
def force_push_pr(org, repo, pr_number):
    pr_info = query_pr_info(org, repo, pr_number)

    local_branch = os.path.basename(os.getcwd())
    source_branch = pr_info['source_branch']
    source_remote = pr_info['source_remote']

    run_cmd([
        'git', 'push', '-f',
        source_remote,
        f'{local_branch}:{source_branch}'
        ])

# tell github to remove draft status from pr
def undraft_pr(org, repo, pr_number):
    run_cmd([
        'gh', 'pr', 'ready',
        '--repo', f'{org}/{repo}',
        pr_number,
    ])

# tell github to merge PR
def merge_pr(org, repo, pr_number):
    # wait until PR is mereable
    while True:
        query_pr_info.cache_clear()

        pr_info = query_pr_info(org, repo, pr_number)
        if pr_info['pr_mergeable'] is not None \
            and pr_info['pr_rebaseable'] is not None:
            break

        time.sleep(0.1)

    # wait more
    time.sleep(1)

    def retry_fn(output):
        return 'GraphQL: Base branch was modified' in output or \
            'GraphQL: Pull Request is not mergeable' in output

    # tell to merge, retry if needed
    run_cmd([
        'gh', 'pr', 'merge',
        '--repo', f'{org}/{repo}',
        '--rebase',
        '--delete-branch',
        pr_number,
        ],
        retry_fn=retry_fn)

COMMON_LABELS = [
    # category

    ('C-algorithms',             '#ff87ad', 'category: Algorithms and data structures'),
    ('C-android',                '#f7b7e0', 'category: Android development'),
    ('C-api',                    '#006b75', 'category: Public API'),
    ('C-benchmarks',             '#6dc9d1', 'category: Performance benchmarks'),
    ('C-build-system',           '#bfe5bf', 'category: Build scripts'),
    ('C-codecs',                 '#454ed1', 'category: Audio and FEC codecs'),
    ('C-command-line',           '#ebbebf', 'category: Command-line tools'),
    ('C-continuous-integration', '#daf2da', 'category: Continuous integration'),
    ('C-documentation',          '#1d4299', 'category: Documentation improvements'),
    ('C-dsp',                    '#ef83f7', 'category: Digital sound processing'),
    ('C-grpc',                   '#006b75', 'category: gRPC support'),
    ('C-networking',             '#3fffb8', 'category: Network and streaming'),
    ('C-packaging',              '#1d76db', 'category: Packaging scripts'),
    ('C-performance',            '#fef2c0', 'category: Profiling and optimizations'),
    ('C-portability',            '#fad8c7', 'category: Cross-platform support'),
    ('C-refactoring',            '#d4c5f9', 'category: Refactoring'),
    ('C-rest-api',               '#006b75', 'category: REST API'),
    ('C-rt-tests',               '#e6f0ff', 'category: Real-time tests'),
    ('C-security',               '#15b58d', 'category: Security or encryption'),
    ('C-sound-io',               '#fcd9f0', 'category: Audio I/O'),
    ('C-storage',                '#acd6e3', 'category: Persistent storage'),
    ('C-system',                 '#ba1856', 'category: Low-level systems programming'),
    ('C-tests',                  '#9fcafc', 'category: Writing or improving tests'),
    ('C-tooling',                '#846a8a', 'category: Improving developer tools'),

    # status

    ('S-borked',             '#b60205', 'status: Review blocked due to quality issues'),
    ('S-cant-reproduce',     '#ef9928', 'status: Unable to reproduce problem'),
    ('S-dismissed',          '#9e5847', 'status: Decided not to implement'),
    ('S-duplicate',          '#ef9928', 'status: Already addressed by another issue or PR'),
    ('S-in-qa',              '#cc317c', 'status: QA in progress'),
    ('S-merged-manually',    '#d4c5f9', 'status: Commits from PR were cherry-picked manually'),
    ('S-moved',              '#ef9928', 'status: Transitioned to another issue or PR'),
    ('S-needs-rebase',       '#ffcfcf', 'status: PR has conflicts and should be rebased'),
    ('S-needs-revision',     '#f4e68b', 'status: Author should revise PR and address feedback'),
    ('S-not-a-bug',          '#ef9928', 'status: Working as intended'),
    ('S-postponed',          '#9e5847', 'status: Postponed for an indefinite period'),
    ('S-ready-for-review',   '#2dc439', 'status: PR can be reviewed'),
    ('S-review-in-progress', '#e9ef99', 'status: PR is being reviewed'),
    ('S-stalled',            '#F9D0C4', 'status: PR or issue is abandoned'),
    ('S-waiting-reply',      '#F9D0C4', 'status: Waiting for response from issue or PR author'),
    ('S-work-in-progress',   '#bfdadc', 'status: PR is still in progress and changing'),

    # standard

    ('contrib',                '#e0ffb2', 'PR not by a maintainer'),
    ('hacktoberfest-accepted', '#edfffe', 'PR approved for Hacktoberfest even if not ready'),
    ('help wanted',            '#9fe84c', 'Looking for contributors'),
    ('invalid',                '#cfcfcf', 'Malformed or low-quality PR or spam'),

]

TOOLKIT_LABELS = [
    ('easy hacks',      '#7fefed', 'Solution requires minimal project context'),
    ('feature request', '#0e8a16', 'Feature requested by user'),
    ('most wanted',     '#ffea00', 'Needed most among other help-wanted issues'),
    ('user report',     '#e0ffb2', 'A bug-report or a feature-request not by a maintainer'),
]

OTHER_LABELS = [
    ('good first issue', '#a5f2ea', 'Task good for newcomers'),
]

# create / update repo labels
def sync_labels(org, repo):
    try:
        response = json.loads(subprocess.run([
            'gh', 'label', 'list', '--json', 'name,color,description',
            '--limit', '999',
            '--repo', f'{org}/{repo}',
            ],
            capture_output=True, text=True, check=True).stdout or '[]')
    except subprocess.CalledProcessError as e:
        error(f'failed to retrieve labels: {e.stderr.strip()}')

    existing_labels = {label['name']: label for label in response}

    target_labels = COMMON_LABELS[:]
    if repo == 'roc-toolkit':
        target_labels += TOOLKIT_LABELS
    else:
        target_labels += OTHER_LABELS

    for label, color, description in target_labels:
        if label in existing_labels:
            if color == '#' + existing_labels[label]['color'] and \
               description == existing_labels[label]['description']:
                continue
            mode = 'edit'
        else:
            mode = 'create'

        run_cmd([
            'gh', 'label', mode,
            '--repo', f'{org}/{repo}',
            label,
            '--description', description,
            '--color', color,
            ])

parser = argparse.ArgumentParser(prog='rgh.py')

common_parser = argparse.ArgumentParser(add_help=False)
common_parser.add_argument('-R', '--repo', type=str, help='github repo')

subparsers = parser.add_subparsers(dest='command')

show_issue_parser = subparsers.add_parser(
    'show_issue', parents=[common_parser],
    help="show issue info")
show_issue_parser.add_argument('issue_number', type=int)
show_issue_parser.add_argument('--json', action='store_true', dest='json',
                            help="output in json format")

show_pr_parser = subparsers.add_parser(
    'show_pr', parents=[common_parser],
    help="show pull request info")
show_pr_parser.add_argument('pr_number', type=int)
show_pr_parser.add_argument('--json', action='store_true', dest='json',
                            help="output in json format")

merge_pr_parser = subparsers.add_parser(
    'merge_pr', parents=[common_parser],
    help="squash-merge or rebase-merge pull request")
merge_pr_parser.add_argument('pr_number', type=int)
merge_pr_parser.add_argument('--rebase', action='store_true',
                             help='merge using rebase')
merge_pr_parser.add_argument('--squash', action='store_true',
                             help='merge using squash')
merge_pr_parser.add_argument('-t', '--title', dest='title',
                             help='overwrite commit message title')
merge_pr_parser.add_argument('--issue', type=int, dest='issue_number',
                             help="overwrite issue to link with")
merge_pr_parser.add_argument('--no-issue', action='store_true', dest='no_issue',
                             help="don't link issue")
merge_pr_parser.add_argument('-m', '--milestone', type=str, dest='milestone_name',
                             help="overwrite issue milestone")
merge_pr_parser.add_argument('-M', '--no-milestone', action='store_true', dest='no_milestone',
                             help="don't set issue milestone")
merge_pr_parser.add_argument('--ignore-actions', action='store_true', dest='ignore_actions',
                             help="proceed even if pr github actions are failed")
merge_pr_parser.add_argument('--ignore-state', action='store_true', dest='ignore_state',
                             help="proceed even if pr is closed or draft")
merge_pr_parser.add_argument('--ignore-review', action='store_true', dest='ignore_review',
                             help="proceed even if pr has requested changes")
merge_pr_parser.add_argument('--no-push', action='store_true', dest='no_push',
                             help="don't actually push and merge anything")
merge_pr_parser.add_argument('-y', '--yes', action='store_true', dest='yes',
                             help="don't ask for confirmation, always assume yes")
merge_pr_parser.add_argument('-n', '--dry-run', action='store_true', dest='dry_run',
                             help="don't actually run commands, just print them")

sync_labels_parser = subparsers.add_parser(
    'sync_labels', parents=[common_parser],
    help="create or update repo labels")
sync_labels_parser.add_argument('-n', '--dry-run', action='store_true', dest='dry_run',
                                 help="don't actually run commands, just print them")

args = parser.parse_args()

if hasattr(args, 'dry_run'):
    DRY_RUN = args.dry_run

if hasattr(args, 'yes'):
    ASK = not args.yes

colorama.init()
check_tools()

org, repo = parse_repo(args.repo)

if args.command == 'show_issue':
    show_issue(org, repo, args.issue_number, args.json)
    sys.exit(0)

if args.command == 'show_pr':
    show_pr(org, repo, args.pr_number, args.json)
    sys.exit(0)

if args.command == 'merge_pr':
    if int(bool(args.rebase)) + int(bool(args.squash)) != 1:
        error("either --rebase or --squash should be specified")
    verify_pr(org, repo, args.pr_number, args.issue_number,
              args.milestone_name, args.no_issue, args.no_milestone,
              args.ignore_actions, args.ignore_state, args.ignore_review)
    # create new worktree in /tmp, where we'll checkout pr's branch
    orig_path = enter_worktree()
    merged = False
    try:
        checkout_pr(org, repo, args.pr_number)
        pr_ref = remember_ref()
        # first update metadata, so that subsequent calls to query_xxx_info()
        # will return correct values
        update_pr_metadata(org, repo, args.pr_number, args.issue_number,
                           args.milestone_name, args.no_issue, args.no_milestone)
        # ensure that all commits we're going to manipulate are available locally
        fetch_pr_commits(org, repo, args.pr_number)
        if args.squash:
            # if we're going to squash-merge, then squash commits before rebasing
            # squash-merge may work even when rebase-merge produces conflicts
            squash_pr_commits(org, repo, args.pr_number, args.title, args.no_issue)
        # no matter if we do squash-merge or rebase-merge, rebase pr on target
        rebase_pr_commits(org, repo, args.pr_number)
        if args.rebase:
            # if we're doing rebase-merge, we must preserve original commits,
            # but ensure that each commit message has correct prefix
            reword_pr_commits(org, repo, args.pr_number, args.title, args.no_issue)
        # show edited history
        print_pr_commits(org, repo, args.pr_number)
        if not args.no_push:
            ask('Force-push and merge?')
            force_push_pr(org, repo, args.pr_number)
            if args.ignore_state:
                undraft_pr(org, repo, args.pr_number)
            merge_pr(org, repo, args.pr_number)
            merged = True
    finally:
        # remove worktree in /tmp
        leave_worktree(orig_path)
        if merged:
            # delete temp branch (only on success)
            delete_ref(pr_ref)
    sys.exit(0)

if args.command == 'sync_labels':
    sync_labels(org, repo)
    sys.exit(0)
