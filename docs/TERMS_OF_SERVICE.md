# Terms of Service and Acceptable Use for Muxivo Core

**Effective Date:** July 10, 2026  
**Last Updated:** July 23, 2026

These terms govern use of Muxivo Core, including the local API, moderation
pipeline, rules, policies, trained model artifacts, scripts, and documentation.

## 1. Service Description

Muxivo Core provides local moderation analysis for platform messages. It may
return labels, confidence values, risk scores, explanations, and recommended
actions.

It is designed to be integrated with platform adapters such as Muxivo Discord, Discord
bots, Telegram bots, dashboards, and internal moderation tools.

## 2. Administrator Responsibilities

Operators and server administrators are responsible for:

- choosing which channels or communities are covered;
- configuring policies and thresholds;
- reviewing destructive action limits before production use;
- informing users when AI moderation is active;
- handling appeals, false positives, and false negatives;
- securing API keys, database credentials, model files, logs, and backups.

When an integration supports automated enforcement, the operator must keep the
platform adapter in control of Discord- or platform-specific permissions. The
API may recommend an action; the adapter and the server policy decide whether
to execute it. Timeout, kick, ban, role changes, and message deletion must be
explicitly enabled by the relevant platform administrator and tested in a
non-production environment first.

## 3. Acceptable Use

You may use Muxivo Core, with proper commercial permission, to:

- moderate communities you own or administer;
- detect spam, scams, abuse, unsafe content, and policy violations;
- support human moderation review;
- run local inference and health checks;
- evaluate and improve moderation policies for the licensed deployment.

## 4. Prohibited Use

You may not use Muxivo Core to:

- violate laws, platform rules, or privacy rights;
- harass, discriminate against, or target users unfairly;
- secretly surveil communities without a lawful basis;
- sell, redistribute, or host the software without a commercial license;
- publish private logs, datasets, or model artifacts outside the licensed scope;
- claim AI decisions are infallible or deny reasonable human review;
- bypass API keys, access controls, or deployment security.

## 5. AI Limitations and Automated Enforcement

Muxivo Core is an assistive system. It may be wrong. Operators must use human
judgment for severe penalties, appeals, edge cases, and policy changes.

Classifier output is not a command to punish a user. Labels, scores,
confidence, recommendations, and rules can be incomplete, delayed, or
incorrect. Integrations should support review workflows, audit records, and
server-controlled enforcement modes such as recommendation-only, limited, and
explicitly elevated automation.

The service does not directly control Discord roles, delete Discord messages,
or issue Discord punishments. Those actions are performed by the platform
adapter under that platform's permissions and policies.

## 6. External Integrations

The default design is local/self-hosted. If an operator connects third-party
model providers, storage, monitoring, or platform APIs, that operator is
responsible for complying with those third-party terms and updating user-facing
privacy notices.

## 7. License

This project is proprietary commercial software. Use is allowed only under the
rights granted in a separate written commercial license or contract.

See [LICENSE](../LICENSE) and [COMMERCIAL_LICENSE.md](../COMMERCIAL_LICENSE.md).

## 8. Availability And Warranty

The software is provided "as is". No guarantee is made for uninterrupted
operation, model accuracy, moderation correctness, compatibility, or data
recovery.

## 9. Changes

These terms may be updated when features, infrastructure, laws, or deployment
requirements change.
