"""Measure S7 query latency over real HTTP, including serialization/network overhead."""
import argparse
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import httpx


def main():
    parser = argparse.ArgumentParser(description='S7 QR1 local measured scenario')
    parser.add_argument('--url',default='http://localhost:8087')
    parser.add_argument('--latitude',type=float,required=True)
    parser.add_argument('--longitude',type=float,required=True)
    parser.add_argument('--radius-m',type=float,default=2500)
    parser.add_argument('--fuel-type',default='regular_87')
    parser.add_argument('--requests',type=int,default=100)
    parser.add_argument('--workers',type=int,default=10)
    args=parser.parse_args()
    if args.requests <= 0 or args.workers <= 0:
        parser.error('--requests and --workers must be positive')
    urls=[
        ('/v1/gas-stations',{'latitude':args.latitude,'longitude':args.longitude,
                             'radius_m':args.radius_m}),
        ('/v1/gas-prices/comparisons',{'latitude':args.latitude,'longitude':args.longitude,
                                       'radius_m':args.radius_m,'fuel_type':args.fuel_type}),
    ]
    for endpoint,query in urls:
        def once(_):
            started=time.perf_counter()
            try:
                with httpx.Client(timeout=5) as client:
                    result=client.get(args.url+endpoint,params=query)
                    result.raise_for_status()
                    result.json()
                return round((time.perf_counter()-started)*1000,3),200
            except httpx.HTTPError as error:
                return round((time.perf_counter()-started)*1000,3),str(error)
        # Warm up before collecting latency samples.
        once(-1)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results=list(pool.map(once,range(args.requests)))
        successes=sorted(latency for latency,status in results if status==200)
        failures=sum(status!=200 for _,status in results)
        output={'endpoint':endpoint,'requests':args.requests,'workers':args.workers,
                'successes':len(successes),'failures':failures,'latency_target_ms':1500,
                'p50_ms':statistics.median(successes) if successes else None,
                'p95_ms':successes[max(0,math.ceil(len(successes)*0.95)-1)] if successes else None,
                'max_ms':max(successes) if successes else None,
                'successful_within_1500ms':sum(duration<=1500 for duration in successes)}
        print(json.dumps(output,indent=2))


if __name__=='__main__':
    main()
