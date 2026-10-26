"""Start the close collection on time despite GitHub's late schedule starts.

The workflow is scheduled 15-35 minutes before a market's publication slot
(wamo_runtime.SLOTS). This waits until the slot, then the normal run begins.
Already past the slot, or no slot soon (holiday, other DST candidate): no wait.
"""
import argparse
import time
from datetime import datetime

from wamo_runtime import KST, UTC, next_slot


def wait_seconds(market, now, max_minutes=60):
    slot = next_slot(market, now)
    seconds = (slot - now).total_seconds() + 20  # a few seconds past the slot
    return (seconds if 0 < seconds <= max_minutes * 60 else 0), slot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--market', required=True, choices=['KR', 'US'])
    parser.add_argument('--max-minutes', type=float, default=60)
    args = parser.parse_args()
    seconds, slot = wait_seconds(args.market, datetime.now(UTC), args.max_minutes)
    if not seconds:
        print(f'{args.market} 대기 없음 · 다음 게시 슬롯 {slot.astimezone(KST):%m-%d %H:%M} KST', flush=True)
        return
    print(f'{args.market} 게시 슬롯 {slot.astimezone(KST):%H:%M} KST까지 {seconds / 60:.1f}분 대기', flush=True)
    time.sleep(seconds)


if __name__ == '__main__':
    main()
