# Privacy Policy for Muxivo Core

**Effective Date:** July 10, 2026  
**Last Updated:** July 23, 2026

This policy explains what data Muxivo Core processes when it is used as a
local/self-hosted moderation API.

## 1. Operator

Project operator: **6oT9lpa / Muxivo Core project team**.

The organization or server administrator deploying Muxivo Core is responsible
for choosing which platform messages are sent to the API and for informing users
when AI moderation is active.

## 2. Data Processed

Muxivo Core may process:

- platform name, guild/server/chat ID, channel ID, message ID, and user ID;
- message text and normalized text;
- timestamps and request correlation IDs;
- user context such as account age, member age, roles, and recent behavior when
  provided by the calling platform;
- reply context, including referenced message ID, author ID, and text, when the
  calling platform sends a Discord reply for contextual classification;
- bounded recent-message text and timestamps from the same author when the
  calling platform enables flood or spam analysis;
- policy context such as guild, channel, role, and user scope;
- rule matches, labels, confidence values, risk scores, reason codes, and
  proposed and final decision actions, plus action-result status supplied by
  the calling platform;
- model metadata, model version, latency, and error details;
- technical logs required for debugging and reliability.

If media/OCR features are enabled in a deployment, the service may also process
attachment metadata, hashes, OCR text, and media analysis signals.

## 3. Purpose

Data is used to:

- classify moderation risk;
- apply preprocessing rules and model inference;
- resolve moderation policies;
- return decisions to the calling platform adapter;
- evaluate reply, spam, and flood context only when supplied by the configured
  platform adapter;
- support audit, debugging, evaluation, regression checks, and model
  improvement;
- monitor service health and reliability.

## 4. Local Processing

The intended production deployment is local/self-hosted. By default, AI
Moderator does not need to send message content to a commercial third-party AI
API. Model inference can run locally on CPU or CUDA GPU.

If a deployment enables external services later, the operator must document that
change and update this policy for affected users.

## 5. Retention

Retention depends on deployment configuration. Technical logs, audit records,
dataset exports, and policy records should be retained only as long as needed
for moderation, security, debugging, training, or legal requirements.

For a Discord deployment, the adapter—not this API—may separately keep
temporary role-restoration records required to return eligible roles after a
timeout. Those records are governed by the adapter's privacy policy.

Release archives and backups should exclude `.env`, logs, virtual environments,
runtime data, and model directories unless an operator intentionally backs them
up under a protected process.

## 6. Access And Deletion

Requests for access, correction, or deletion should include:

- platform user ID;
- server/guild/chat ID;
- approximate message time or message ID if available;
- requested action: export, correct, delete, or disable.

The operator may need to verify that the requester is the relevant user or an
authorized administrator.

## 7. Security

Recommended safeguards:

- run the API on localhost or a private network;
- require an internal API key;
- keep secrets in `.env`;
- restrict database access;
- restrict model and dataset directories;
- log security-relevant failures;
- protect backups and exports;
- keep NVIDIA/CUDA and Python dependencies updated.

## 8. Limitations

Muxivo Core may produce false positives and false negatives. It should assist
moderation teams, not replace human judgment or appeals.

## 9. Changes

This policy may be updated when features, infrastructure, laws, or deployment
requirements change.
