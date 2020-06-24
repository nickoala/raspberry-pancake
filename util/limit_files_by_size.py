import sys
import os
import glob
from functools import reduce
from itertools import dropwhile

"""
Restrict total file sizes in directories. Remove older files.

$ python3 limit_files_by_size.py dirA 1200000 dirB 2000000

This command keeps total file sizes in dirA under 1200000 bytes, and dirB under
2000000 bytes. More directories are allowed.
"""

args = sys.argv[1:]

pairs = [(args[i], int(args[i+1])) for i in range(0, len(args), 2)]

for dir, max_total_size in pairs:
    # Sort files from newest to oldest
    files = sorted(glob.iglob(os.path.join(dir, '*')),
                key=lambda x: os.stat(x).st_mtime,
                reverse=True)

    # Accumulate total file sizes
    total_sizes = reduce(
                lambda sizes, f: sizes + [sizes[-1] + os.stat(f).st_size],
                files, [0])[1:]  # drop first element [0]

    # Remove files beyond max total size
    for f,_ in dropwhile(
                lambda x: x[1] <= max_total_size,
                zip(files, total_sizes)):
        os.remove(f)
