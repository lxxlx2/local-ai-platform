# Retention policy

Public development defaults: messages/sessions 30 days, files 24 hours, outputs 7 days. Owner data is user-managed until a remote-provider policy is configured. Blocked secrets have zero retention: no model input, message persistence, summary, embedding, raw log, or event payload. Deletion marks records deleted and removes them from active retrieval; remote adapters must implement the same logical deletion workflow.
