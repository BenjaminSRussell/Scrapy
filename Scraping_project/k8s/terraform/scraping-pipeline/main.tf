locals {
  chart_path = abspath("${path.module}/../../helm/scraping-pipeline")

  # dev is values.yaml (profile development). small and prod are the overlays
  # documented in Scraping_project/DEPLOYMENT.md. Later files win inside Helm.
  profile_overlay = {
    dev   = ""
    small = "${local.chart_path}/values-small.yaml"
    prod  = "${local.chart_path}/values-prod.yaml"
  }

  values_files = compact([
    "${local.chart_path}/values.yaml",
    local.profile_overlay[var.profile],
  ])

  delta_storage_class = (
    var.delta_lake_storage_class != "" ? var.delta_lake_storage_class : var.storage_class
  )

  # Strings stay strings. Booleans and the Redis port use type auto so Helm
  # does not treat the string "false" as true.
  helm_sets = concat(
    [
      { name = "scrapyApp.image.repository", value = var.crawler_image_repository, type = "string" },
      { name = "scrapyApp.image.tag", value = var.crawler_image_tag, type = "string" },
      { name = "stage2Worker.image.repository", value = var.crawler_image_repository, type = "string" },
      { name = "stage2Worker.image.tag", value = var.crawler_image_tag, type = "string" },
      { name = "stage3Worker.image.repository", value = var.crawler_image_repository, type = "string" },
      { name = "stage3Worker.image.tag", value = var.crawler_image_tag, type = "string" },
      { name = "metricsExporter.image.repository", value = var.metrics_image_repository, type = "string" },
      { name = "metricsExporter.image.tag", value = var.metrics_image_tag, type = "string" },
      { name = "kafkaDeltaIngestor.image.repository", value = var.ingestor_image_repository, type = "string" },
      { name = "kafkaDeltaIngestor.image.tag", value = var.ingestor_image_tag, type = "string" },
    ],
    var.stage4_enabled ? [
      { name = "stage4Worker.enabled", value = "true", type = "auto" },
      { name = "stage4Worker.image.repository", value = var.stage4_image_repository, type = "string" },
      { name = "stage4Worker.image.tag", value = var.stage4_image_tag, type = "string" },
    ] : [],
    var.redis_host == "" ? [] : [
      { name = "redis.enabled", value = "false", type = "auto" },
      { name = "redis.external.host", value = var.redis_host, type = "string" },
      { name = "redis.external.port", value = tostring(var.redis_port), type = "auto" },
    ],
    var.grafana_host == "" ? [] : [
      { name = "ingress.hosts[0].host", value = var.grafana_host, type = "string" },
    ],
    var.profile == "prod" && var.grafana_host != "" ? [
      { name = "ingress.tls[0].hosts[0]", value = var.grafana_host, type = "string" },
    ] : [],
    var.storage_class == "" ? [] : [
      { name = "global.storageClass", value = var.storage_class, type = "string" },
    ],
    local.delta_storage_class == "" ? [] : [
      { name = "deltaLake.persistence.storageClass", value = local.delta_storage_class, type = "string" },
    ],
  )
}

resource "helm_release" "scraping_pipeline" {
  name             = var.release_name
  namespace        = var.namespace
  create_namespace = var.create_namespace
  chart            = local.chart_path

  # Re-apply our values files on upgrade instead of keeping a drifted --set.
  reset_values    = true
  wait            = var.wait
  timeout         = var.timeout_seconds
  atomic          = false
  cleanup_on_fail = false

  values = [for path in local.values_files : file(path)]

  dynamic "set" {
    for_each = local.helm_sets
    content {
      name  = set.value.name
      value = set.value.value
      type  = set.value.type
    }
  }

  dynamic "set_list" {
    for_each = var.redis_host != "" && length(var.redis_egress_cidrs) > 0 ? [var.redis_egress_cidrs] : []
    content {
      name  = "redis.external.egressCIDRs"
      value = set_list.value
    }
  }

  lifecycle {
    precondition {
      condition = (
        var.stage4_enabled == false
        || (
          length(var.stage4_image_repository) > 0
          && length(var.stage4_image_tag) > 0
          && var.stage4_image_tag != "latest"
        )
      )
      error_message = "stage4_enabled requires stage4_image_repository and a non-latest stage4_image_tag."
    }

    precondition {
      condition = (
        var.profile != "prod"
        || (
          var.redis_host != ""
          && var.redis_host != "redis.example.edu"
          && length(var.redis_egress_cidrs) > 0
          && !contains(var.redis_egress_cidrs, "203.0.113.0/24")
          && var.grafana_host != ""
          && var.grafana_host != "grafana.example.edu"
          && var.grafana_host != "grafana.local"
        )
      )
      error_message = "profile prod requires redis_host (not redis.example.edu), redis_egress_cidrs (not 203.0.113.0/24), and grafana_host (not grafana.example.edu). Do not set REDIS_URL. Passwords stay in Kubernetes Secrets."
    }
  }
}
