# -*- coding: utf-8 -*-
"""Analyze log for 2026-02-19: users, activity, topics."""
import sys
from collections import defaultdict

path = 'data/res - 2026-02-20T031559.655.txt'
bot_id = '8211322326'
day = '2026-02-19'

# per user: attempts, correct, starts, topics set
users = defaultdict(lambda: {'attempts': 0, 'correct': 0, 'starts': 0, 'topics': set()})
# topic -> attempts, correct
topics = defaultdict(lambda: {'attempts': 0, 'correct': 0})
# hour -> count of answer lines
hour_attempts = defaultdict(int)
hour_correct = defaultdict(int)
# usernames (id -> username)
usernames = {}

with open(path, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.rstrip()
        if not line or not line[0].isdigit():
            continue
        parts = line.split('\t')
        if len(parts) < 4:
            continue
        ts = parts[0]
        if not ts.startswith(day):
            continue
        user_id = parts[1]
        if user_id == bot_id:
            continue
        username = parts[2] if len(parts) > 2 else ''
        if username and username != 'None':
            usernames[user_id] = username
        action = parts[3]
        if action == 'start':
            users[user_id]['starts'] += 1
            continue
        if len(parts) >= 5 and parts[-1] in ('0', '1'):
            correct = parts[-1] == '1'
            topic = parts[3] if len(parts) >= 4 else ''
            try:
                h = int(ts[11:13])
                hour_attempts[h] += 1
                if correct:
                    hour_correct[h] += 1
            except (ValueError, IndexError):
                pass
            users[user_id]['attempts'] += 1
            if correct:
                users[user_id]['correct'] += 1
            if topic and not topic.isdigit() and topic not in ('message', 'next', 'end', 'status'):
                users[user_id]['topics'].add(topic)
                topics[topic]['attempts'] += 1
                if correct:
                    topics[topic]['correct'] += 1

# Output
out = []
out.append('=== 19 feb 2026 ===')
out.append('Total users (with any activity): %d' % len(users))
active = [u for u, d in users.items() if d['attempts'] > 0]
out.append('Users who answered at least 1 task: %d' % len(active))
only_start = [u for u, d in users.items() if d['starts'] > 0 and d['attempts'] == 0]
out.append('Users only /start, no answers: %d' % len(only_start))

attempts_per_user = [d['attempts'] for d in users.values() if d['attempts'] > 0]
if attempts_per_user:
    out.append('')
    out.append('Attempts per active user: min=%d max=%d avg=%.1f' % (
        min(attempts_per_user), max(attempts_per_user), sum(attempts_per_user) / len(attempts_per_user)))
correct_per_user = [d['correct'] for d in users.values() if d['attempts'] > 0]
if correct_per_user:
    out.append('Correct per active user: min=%d max=%d avg=%.1f' % (
        min(correct_per_user), max(correct_per_user), sum(correct_per_user) / len(correct_per_user)))

# Top users by attempts
by_attempts = sorted([(uid, d['attempts'], d['correct']) for uid, d in users.items() if d['attempts'] > 0],
                     key=lambda x: -x[1])
out.append('')
out.append('Top 10 by attempts (user_id attempts correct):')
for uid, att, cor in by_attempts[:10]:
    un = usernames.get(uid, uid)
    out.append('  %s  %d  %d' % (un, att, cor))

# Topics
out.append('')
out.append('By topic (attempts / correct / %%):')
for t in sorted(topics.keys(), key=lambda x: -topics[x]['attempts']):
    d = topics[t]
    pct = 100 * d['correct'] / d['attempts'] if d['attempts'] else 0
    out.append('  %s: %d / %d (%.1f%%)' % (t, d['attempts'], d['correct'], pct))

# Hour distribution (peak hours)
out.append('')
out.append('Peak hours (attempts):')
for h in sorted(hour_attempts.keys(), key=lambda x: -hour_attempts[x])[:6]:
    out.append('  %02d:00 - %d attempts, %d correct' % (h, hour_attempts[h], hour_correct.get(h, 0)))

# Users with username vs None
with_username = sum(1 for uid in active if usernames.get(uid))
out.append('')
out.append('Active users with username: %d, without (None): %d' % (with_username, len(active) - with_username))

text = '\n'.join(out)
print(text)
with open('data/analysis_19feb.txt', 'w', encoding='utf-8') as f:
    f.write(text)
