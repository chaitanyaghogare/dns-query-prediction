# Machine Learning Context

## Prediction Task

Given historical DNS query behavior, predict whether a domain
will be queried again within a specified future time window.

## Possible Approaches

1. Logistic Regression
2. Random Forest
3. XGBoost
4. LightGBM
5. Time-series approaches

Start with simple baselines before complex models.

## Candidate Features

- query frequency
- time since last query
- queries in last 1 minute
- queries in last 5 minutes
- queries in last 30 minutes
- hour of day
- day of week
- domain-specific historical frequency
- TTL
- cache status

## Evaluation

ML metrics:
- Precision
- Recall
- F1
- ROC-AUC

System metrics:
- Cache hit rate
- Average latency
- P95 latency
- Upstream query count

## Important

A model with high classification accuracy is not automatically
a successful solution.

System-level cache performance is the primary evaluation.