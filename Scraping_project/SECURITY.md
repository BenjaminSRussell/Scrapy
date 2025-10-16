# Security Guide

This document outlines security considerations, best practices, and known issues for the Scraping Pipeline project.

## Table of Contents

- [Critical Security Requirements](#critical-security-requirements)
- [Deployment Security](#deployment-security)
- [Data Security](#data-security)
- [Network Security](#network-security)
- [Monitoring and Incident Response](#monitoring-and-incident-response)
- [Known Security Issues](#known-security-issues)
- [Security Checklist](#security-checklist)

---

## Critical Security Requirements

### 1. Secrets Management

**NEVER commit secrets to version control.** All sensitive credentials must be managed securely.

#### For Local Development (Docker Compose):

1. Create a `.env` file in the project root (this file is gitignored):
   ```bash
   # Database credentials
   POSTGRES_PASSWORD=your_secure_password_here

   # Grafana admin credentials
   GRAFANA_ADMIN_PASSWORD=your_secure_password_here
   ```

2. The docker-compose.yml has been configured to require these environment variables.

#### For Kubernetes/Production:

**Option 1: External Secrets Manager (RECOMMENDED)**

The Helm chart is configured by default to use external secrets (`useExternalSecrets: true`). Use one of:
- AWS Secrets Manager
- Google Cloud Secret Manager
- Azure Key Vault
- HashiCorp Vault
- External Secrets Operator

**Option 2: Manual Secret Creation**

Before deploying with Helm:
```bash
kubectl create secret generic postgres-credentials \
  --from-literal=password=<YOUR_SECURE_PASSWORD> \
  --from-literal=POSTGRES_PASSWORD=<YOUR_SECURE_PASSWORD> \
  --from-literal=DB_PASSWORD=<YOUR_SECURE_PASSWORD>

kubectl create secret generic grafana-credentials \
  --from-literal=admin-user=admin \
  --from-literal=admin-password=<YOUR_SECURE_PASSWORD>
```

**Option 3: Override During Helm Install (NOT RECOMMENDED)**

Only for development/testing:
```bash
helm install scraping-pipeline k8s/helm/scraping-pipeline \
  --set secrets.useExternalSecrets=false \
  --set secrets.postgres.password=<YOUR_SECURE_PASSWORD> \
  --set secrets.grafana.adminPassword=<YOUR_SECURE_PASSWORD>
```

### 2. Image Security

**NEVER use `latest` tags in production.** All container images must use specific, immutable tags.

#### Best Practices:

1. **Use Git commit SHAs for application images:**
   ```bash
   docker tag myapp:latest myregistry/myapp:$(git rev-parse --short HEAD)
   docker push myregistry/myapp:$(git rev-parse --short HEAD)
   ```

2. **Use semantic versioning for releases:**
   ```bash
   docker tag myapp:latest myregistry/myapp:v1.2.3
   docker push myregistry/myapp:v1.2.3
   ```

3. **Update Helm values.yaml before deploying:**
   ```yaml
   scrapyApp:
     image:
       repository: ghcr.io/yourorg/scraping-pipeline
       tag: "a1b2c3d"  # Git commit SHA or version
   ```

4. **Pin third-party image versions:**
   - ✅ `redis:7.4-alpine` (good - specific version)
   - ❌ `redis:latest` (bad - unpredictable)

### 3. Schema Validation

The kafka-delta-ingestor now validates all incoming messages against a strict JSON schema. This prevents data corruption from malformed or malicious input.

**Schema Requirements:**
- `url` (required): Must be a non-empty string
- `scraped_at_utc` (required): Must be an ISO 8601 timestamp
- `spider_name` (required): Must be a non-empty string
- `title` (optional): String or null
- `content` (optional): String or null
- `pipeline_version` (optional): String or null

Invalid messages are:
- Logged with detailed error information
- Tracked in metrics (`errors.schema_validation_failed`)
- Dropped (not written to Delta Lake)
- Counted as dropped items in Redis metrics

**TODO:** Implement dead-letter queue (DLQ) for invalid messages to enable later inspection and debugging.

---

## Deployment Security

### Kubernetes Security

#### Namespace Isolation

Deploy the application in a dedicated namespace (not `default`):

```bash
# Using the deployment script
./scripts/k8s_reset_and_deploy.sh production

# Or manually
helm install scraping-pipeline k8s/helm/scraping-pipeline \
  --namespace scraping-pipeline \
  --create-namespace
```

#### Network Policies

Enable network policies in Helm values to restrict pod-to-pod communication:

```yaml
networkPolicy:
  enabled: true
  policyTypes:
    - Ingress
    - Egress
```

#### RBAC

Ensure the Helm chart creates appropriate ServiceAccounts and limits permissions. Review and customize RBAC rules in `k8s/helm/scraping-pipeline/templates/serviceaccount.yaml`.

#### Resource Limits

All pods have resource limits defined. These prevent:
- Resource exhaustion attacks
- Noisy neighbor problems
- Cascading failures

Review and adjust limits based on your workload:

```yaml
resources:
  limits:
    cpu: 1000m
    memory: 1Gi
  requests:
    cpu: 250m
    memory: 256Mi
```

### Storage Security

#### ReadWriteMany (RWX) Volumes

The Delta Lake volume requires RWX access. This is a security consideration:

**Risks:**
- Multiple pods can write to the same volume
- Potential for data corruption if not properly managed
- Shared volume could be exploited if a pod is compromised

**Mitigations:**
1. Use Delta Lake's ACID transactions (already implemented)
2. Limit which pods have access to the volume
3. Use encryption at rest for the underlying storage
4. Consider using object storage (S3, GCS, Azure Blob) instead of PVCs in production

#### Encryption

**At Rest:**
```yaml
# For AWS EBS volumes
storageClassName: encrypted-gp3

# For GCP Persistent Disks
storageClassName: encrypted-pd-ssd
```

**In Transit:**
- Enable TLS for Kafka brokers
- Use SSL for PostgreSQL connections
- Enable Redis AUTH and TLS

---

## Data Security

### Scraped Data

**Considerations:**
- Scraped content may contain PII (Personally Identifiable Information)
- Content may be subject to copyright
- Some URLs may be sensitive (e.g., contain tokens)

**Best Practices:**
1. **Data Minimization:** Only scrape what you need
2. **Access Control:** Limit who can read from Delta Lake tables
3. **Retention Policies:** Implement data lifecycle management
   ```python
   # Vacuum old data (7 days default)
   delta_manager.vacuum_all_tables(retention_hours=168)
   ```
4. **Anonymization:** Remove or hash PII before storage
5. **Audit Logging:** Track who accesses what data

### Database Security

**PostgreSQL:**
- Use strong passwords (enforced by Helm chart)
- Enable SSL/TLS in production
- Restrict network access (use NetworkPolicies)
- Regular backups with encryption

**Redis:**
- Enable AUTH (set `redis.password` in config)
- Disable dangerous commands in production
- Use Redis 6+ with ACLs for fine-grained permissions

---

## Network Security

### Ingress Security

If exposing Grafana via Ingress:

1. **Enable TLS:**
   ```yaml
   ingress:
     tls:
       - secretName: grafana-tls
         hosts:
           - grafana.example.com
   ```

2. **Use authentication middleware:**
   - OAuth2 proxy
   - Basic auth (minimum)
   - SSO integration

3. **Rate limiting:**
   ```yaml
   annotations:
     nginx.ingress.kubernetes.io/rate-limit: "10"
   ```

### Service Mesh (Optional)

For advanced security, consider using a service mesh like Istio or Linkerd:
- Automatic mTLS between services
- Fine-grained authorization policies
- Traffic encryption
- Request authentication

---

## Monitoring and Incident Response

### Security Monitoring

**Metrics to Track:**
- `errors.schema_validation_failed`: Spike may indicate attack
- `errors.parse_failed`: Malformed input attempts
- Failed authentication attempts (Grafana, PostgreSQL)
- Unusual traffic patterns

**Alerts to Configure:**
```yaml
# monitoring/alerting/rules.yml
- alert: SchemaValidationSpike
  expr: rate(errors_schema_validation_failed[5m]) > 10
  for: 5m
  annotations:
    summary: "High rate of schema validation failures"
    description: "May indicate malicious input or spider misconfiguration"
```

### Incident Response Plan

1. **Isolate:** Use NetworkPolicies to block compromised pods
2. **Investigate:** Check logs and metrics in Grafana
3. **Contain:** Scale down affected deployments
4. **Remediate:** Apply patches, rotate secrets
5. **Post-mortem:** Document and update security measures

---

## Known Security Issues

### High-Priority Issues (RESOLVED)

✅ **Insecure Default Passwords** (Fixed in commit XXX)
- **Issue:** Helm values.yaml contained hardcoded passwords
- **Resolution:** Removed defaults, added validation, documented secure practices
- **Status:** FIXED - deployment now fails if insecure passwords are used

✅ **Missing Schema Validation** (Fixed in commit XXX)
- **Issue:** kafka-delta-ingestor did not validate incoming messages
- **Resolution:** Implemented JSON Schema validation with detailed error reporting
- **Status:** FIXED - all messages validated before writing to Delta Lake

✅ **Inefficient Delta Lake Writes** (Fixed in commit XXX)
- **Issue:** PyArrow schema inferred on every write operation
- **Resolution:** Cache schema and use columnar format for subsequent writes
- **Status:** FIXED - significant performance improvement

### Medium-Priority Issues

⚠️ **No Dead Letter Queue**
- **Issue:** Invalid messages are dropped without preservation
- **Impact:** Cannot inspect or replay failed messages
- **Recommendation:** Add Kafka DLQ topic and write failed messages there
- **Status:** TODO

⚠️ **Hardcoded Kafka Topic**
- **Issue:** Topic name was hardcoded in multiple places
- **Resolution:** Made configurable via config.yml
- **Status:** PARTIALLY FIXED - still need to update all consumers

⚠️ **No Rate Limiting**
- **Issue:** No protection against excessive scraping
- **Impact:** Could cause legal/ethical issues or service degradation
- **Recommendation:** Implement per-domain rate limits
- **Status:** TODO

### Low-Priority Issues

ℹ️ **No Container Image Signing**
- Consider using Sigstore/cosign to sign and verify images
- Prevents supply chain attacks

ℹ️ **No Runtime Security**
- Consider using Falco for runtime threat detection
- Detects anomalous behavior in containers

---

## Security Checklist

### Pre-Deployment

- [ ] All secrets are stored in external secrets manager or manually created
- [ ] No default/insecure passwords are used
- [ ] All container images use specific, immutable tags
- [ ] `.env` file exists and contains secure credentials (local only)
- [ ] Network policies are enabled (production)
- [ ] TLS is configured for Ingress (if used)
- [ ] Resource limits are set appropriately

### Post-Deployment

- [ ] Verify secrets were created correctly
- [ ] Check pod security contexts
- [ ] Test authentication (Grafana, PostgreSQL)
- [ ] Configure security alerts in Prometheus
- [ ] Set up log aggregation and retention
- [ ] Document access control procedures
- [ ] Schedule regular security reviews

### Ongoing

- [ ] Rotate secrets quarterly (at minimum)
- [ ] Update container images monthly
- [ ] Review audit logs weekly
- [ ] Test incident response plan quarterly
- [ ] Update security documentation as changes are made

---

## Reporting Security Issues

If you discover a security vulnerability, please:

1. **DO NOT** open a public issue
2. Email security@example.com (replace with your email)
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will respond within 48 hours and work with you to address the issue.

---

## Additional Resources

- [OWASP Kubernetes Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Kubernetes_Security_Cheat_Sheet.html)
- [CIS Docker Benchmark](https://www.cisecurity.org/benchmark/docker)
- [Kubernetes Security Best Practices](https://kubernetes.io/docs/concepts/security/security-best-practices/)
- [Delta Lake Security](https://docs.delta.io/latest/delta-security.html)

---

**Last Updated:** 2025-10-16
**Document Version:** 1.0
**Maintained By:** Platform Security Team
