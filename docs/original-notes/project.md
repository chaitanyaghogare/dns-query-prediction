# Project Overview

## Problem

Traditional DNS caching relies primarily on TTL-based expiration.
This project investigates whether query prediction can be used to
intelligently optimize DNS cache management.

## Proposed Solution

The system analyzes historical DNS queries and predicts which domains
are likely to be queried again.

Predictions are used to improve cache management by prioritizing
entries that are likely to be requested in the near future.

## Primary Objectives

1. Increase DNS cache hit rate.
2. Reduce average DNS resolution latency.
3. Reduce unnecessary upstream DNS queries.
4. Compare AI-assisted caching against conventional TTL-based caching.

## Key Metrics

- Cache hit rate
- Cache miss rate
- Average query latency
- P95/P99 latency
- Number of upstream queries
- Cache memory utilization
- Prediction precision
- Prediction recall

## Important Constraint

ML prediction accuracy alone is NOT the primary success metric.

The final system must demonstrate improvement in actual DNS
cache performance.