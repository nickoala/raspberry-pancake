import sys
import os
import glob
import shutil
from datetime import datetime
from functools import reduce

"""
Intended to be used on a remote backup location where files are organized into
a YYYY/MM/DD directory tree, this script restricts the number of days present
under each directory, and remove older days.

$ python3 limit_days.py dirA 30 dirB 60

This command keeps the most recent 30 days in dirA and the most recent 60 days
in dirB. More directories are allowed.
"""

args = sys.argv[1:]

pairs = [(args[i], int(args[i+1])) for i in range(0, len(args), 2)]

for dir, number_to_keep in pairs:
    def is_day_dir(path):
        try:
            # match .../2020/02/16/
            datetime.strptime(path, os.path.join(dir, '%Y', '%m', '%d', ''))
        except ValueError:
            return False
        else:
            return True

    def is_month_dir(path):
        try:
            # match .../2020/02/
            datetime.strptime(path, os.path.join(dir, '%Y', '%m', ''))
        except ValueError:
            return False
        else:
            return True

    def is_year_dir(path):
        try:
            # match .../2020/
            datetime.strptime(path, os.path.join(dir, '%Y', ''))
        except ValueError:
            return False
        else:
            return True

    def categorize(path):
        # check path nature in turn
        for i,f in enumerate([is_day_dir, is_month_dir, is_year_dir]):
            if f(path):
                return i
        return path

    def sort(groups, path):
        try:
            # sort directories into sets
            c = categorize(path)
            groups[c].add(path)
        except TypeError:
            pass
        finally:
            return groups

    days, months, years = reduce(sort,
            # By having '/' at the end, select only directories.
            glob.iglob(os.path.join(dir, '**', '*', ''), recursive=True),
            (set(), set(), set()))

    # Remove oldest days
    for d in sorted(days, reverse=True)[number_to_keep:]:
        shutil.rmtree(d)

    def is_empty(path):
        return not os.listdir(path)

    # Remove empty months
    for d in filter(is_empty, months):
        os.rmdir(d)

    # Remove empty years
    for d in filter(is_empty, years):
        os.rmdir(d)
