# Muxivo Core Architecture

## Logical components

- `src/contracts/` defines API and internal validation boundaries.
- `src/modules/preprocessing/` normalizes messages and emits explainable
  features: URLs, invites, mentions, duplicates and message-rate signals.
- `src/modules/rules/` combines deterministic and ML-derived signals into a
  risk evaluation.
- `src/modules/decision/` applies the decision policy and builds a recommended
  action bundle.
- `src/modules/dataset/` persists every decision for training and review.
- `src/application/` orchestrates those components for HTTP routes.

## Request lifecycle

1. The API validates a bounded Discord message payload.
2. `TextPreprocessor` creates normalized text and contextual flood features.
3. Rule policies, preprocessing signals, ruBERT and phishing signals are
   evaluated together.
4. `DecisionEngine` returns a recommendation, confidence, severity and plan.
5. `DatasetCollector` persists the full decision before the API responds.

The service returns recommendations only. It never calls Discord or applies a
punishment; the Discord bot's enforcement policy is the final safety boundary.
