"""Official resumable CLI: python -m app.backfill --help."""
import argparse
import json

from app.db import init_db
from app.services import history_backfill as jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create', help='Create a durable plan; default last five years')
    create.add_argument('--start')
    create.add_argument('--end')
    create.add_argument('--min-avg-amount', type=float, default=jobs.config.BACKFILL_MIN_AVG_AMOUNT)
    create.add_argument('--min-observations', type=int, default=120)
    create.add_argument('--stocks', help='Comma-separated IDs for small-sample validation only')
    run = sub.add_parser('run', help='Resume committed checkpoints; keep machine awake')
    run.add_argument('job_id')
    run.add_argument('--max-units', type=int, default=100, help='0=until complete; default=100')
    status = sub.add_parser('status')
    status.add_argument('job_id', nargs='?')
    pause = sub.add_parser('pause')
    pause.add_argument('job_id')
    sub.add_parser('migrate')
    args = parser.parse_args()
    init_db()
    if args.command == 'create':
        out = jobs.create_job(args.start, args.end, args.min_avg_amount, args.min_observations,
                              args.stocks.split(',') if args.stocks else None)
    elif args.command == 'run':
        try:
            out = jobs.run(args.job_id, args.max_units)
        except KeyboardInterrupt:
            print('Interrupted; resume the same job ID.')
            raise SystemExit(130)
    elif args.command == 'status':
        out = jobs.status(args.job_id)
    elif args.command == 'pause':
        out = jobs.pause(args.job_id)
    else:
        out = {'migration': 'ok'}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if out.get('status') == 'failed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
