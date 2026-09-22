"""Thin local Inspect entry point; package entry point is scicode/scicode."""
import argparse
from inspect_ai import eval
from scicode import scicode


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',required=True)
    parser.add_argument('--sandbox',choices=['k8s','docker'],default='k8s')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--include-dev-set',action='store_true')
    parser.add_argument('--provide-scientific-background',action='store_true')
    args=parser.parse_args()
    eval(scicode(sandbox_type=args.sandbox,include_dev_set=args.include_dev_set,
                 provide_scientific_background=args.provide_scientific_background),model=args.model,limit=args.limit)


if __name__=='__main__':main()
