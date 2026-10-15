variable "profile" {
  type        = string
  default     = "small"
  description = "Helm profile. dev is values.yaml only. small and prod are overlays on that file. Sizes are in DEPLOYMENT.md. small is the default so one apply does not start dev replica counts or the prod Kafka RF=3 stack."

  validation {
    condition     = contains(["dev", "small", "prod"], var.profile)
    error_message = "profile must be dev, small, or prod."
  }
}

variable "release_name" {
  type        = string
  default     = "scraping-pipeline"
  description = "Helm release name. Matches k8s/README.md."

  validation {
    condition     = can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.release_name))
    error_message = "release_name must be a lowercase DNS label."
  }
}

variable "namespace" {
  type        = string
  default     = "scraping-pipeline"
  description = "Release namespace. k8s/README.md uses scraping-pipeline. python start.py --env k8s uses scraping; set this to scraping to share that namespace."

  validation {
    condition     = can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.namespace))
    error_message = "namespace must be a lowercase DNS label."
  }
}

variable "create_namespace" {
  type        = bool
  default     = true
  description = "Create the namespace when it is missing. The chart does not create it."
}

variable "kubeconfig_path" {
  type        = string
  default     = ""
  description = "Kubeconfig for an existing cluster. Empty uses the default kubeconfig. This module does not create a cluster."
}

variable "kubeconfig_context" {
  type        = string
  default     = ""
  description = "kubeconfig context. Empty keeps the file's current context."
}

variable "wait" {
  type        = bool
  default     = true
  description = "Wait until the Helm release is ready. A pending PVC or a missing image fails the apply."
}

variable "timeout_seconds" {
  type        = number
  default     = 900
  description = "Helm wait timeout in seconds."

  validation {
    condition     = var.timeout_seconds >= 60 && var.timeout_seconds <= 3600
    error_message = "timeout_seconds must be between 60 and 3600."
  }
}

variable "crawler_image_repository" {
  type        = string
  description = "Image for scrapyApp, stage2Worker, and stage3Worker (Dockerfile crawler target)."

  validation {
    condition = (
      length(var.crawler_image_repository) > 0
      && !strcontains(var.crawler_image_repository, "://")
      && can(regex("^[A-Za-z0-9._:/@-]+$", var.crawler_image_repository))
    )
    error_message = "crawler_image_repository is required and must be an image reference, not a URL."
  }
}

variable "crawler_image_tag" {
  type        = string
  description = "Tag for the crawler image. Do not use latest."

  validation {
    condition     = length(var.crawler_image_tag) > 0 && var.crawler_image_tag != "latest" && can(regex("^[A-Za-z0-9._-]+$", var.crawler_image_tag))
    error_message = "crawler_image_tag is required and must not be latest."
  }
}

variable "metrics_image_repository" {
  type        = string
  description = "Image for metricsExporter (Dockerfile metrics target)."

  validation {
    condition = (
      length(var.metrics_image_repository) > 0
      && !strcontains(var.metrics_image_repository, "://")
      && can(regex("^[A-Za-z0-9._:/@-]+$", var.metrics_image_repository))
    )
    error_message = "metrics_image_repository is required and must be an image reference, not a URL."
  }
}

variable "metrics_image_tag" {
  type        = string
  description = "Tag for the metrics image."

  validation {
    condition     = length(var.metrics_image_tag) > 0 && var.metrics_image_tag != "latest" && can(regex("^[A-Za-z0-9._-]+$", var.metrics_image_tag))
    error_message = "metrics_image_tag is required and must not be latest."
  }
}

variable "ingestor_image_repository" {
  type        = string
  description = "Image for kafkaDeltaIngestor (Dockerfile kafka-delta-ingest target)."

  validation {
    condition = (
      length(var.ingestor_image_repository) > 0
      && !strcontains(var.ingestor_image_repository, "://")
      && can(regex("^[A-Za-z0-9._:/@-]+$", var.ingestor_image_repository))
    )
    error_message = "ingestor_image_repository is required and must be an image reference, not a URL."
  }
}

variable "ingestor_image_tag" {
  type        = string
  description = "Tag for the kafka-delta-ingest image."

  validation {
    condition     = length(var.ingestor_image_tag) > 0 && var.ingestor_image_tag != "latest" && can(regex("^[A-Za-z0-9._-]+$", var.ingestor_image_tag))
    error_message = "ingestor_image_tag is required and must not be latest."
  }
}

variable "stage4_enabled" {
  type        = bool
  default     = false
  description = "Enable stage4Worker. Off unless the PDF/OCR image is set. The chart default is off."
}

variable "stage4_image_repository" {
  type        = string
  default     = ""
  description = "Image for stage4Worker. Required only when stage4_enabled is true."
}

variable "stage4_image_tag" {
  type        = string
  default     = ""
  description = "Tag for the stage 4 image. Required only when stage4_enabled is true."
}

variable "redis_host" {
  type        = string
  default     = ""
  description = "External Redis hostname (REDIS_HOST). Required for profile prod. Hostname only. Do not set REDIS_URL."

  validation {
    condition     = var.redis_host == "" || can(regex("^[A-Za-z0-9.-]+$", var.redis_host))
    error_message = "redis_host must be a hostname. Do not pass a URL or REDIS_URL."
  }
}

variable "redis_port" {
  type        = number
  default     = 6379
  description = "External Redis port (REDIS_PORT). Used only when redis_host is set. Password stays the redis-credentials Secret (REDIS_PASSWORD). REDIS_DB stays the process default unless the chart sets it."

  validation {
    condition     = var.redis_port > 0 && var.redis_port < 65536
    error_message = "redis_port must be between 1 and 65535."
  }
}

variable "redis_egress_cidrs" {
  type        = list(string)
  default     = []
  description = "CIDRs for redis.external.egressCIDRs. Required for profile prod. NetworkPolicy cannot match a DNS name. Do not leave the documentation range 203.0.113.0/24."

  validation {
    condition     = alltrue([for c in var.redis_egress_cidrs : can(cidrhost(c, 0))])
    error_message = "Every redis_egress_cidrs entry must be a CIDR prefix."
  }
}

variable "grafana_host" {
  type        = string
  default     = ""
  description = "Ingress host for Grafana. Required for profile prod. Replace grafana.example.edu. Not a URL."

  validation {
    condition     = var.grafana_host == "" || can(regex("^[A-Za-z0-9.-]+$", var.grafana_host))
    error_message = "grafana_host must be a hostname, not a URL."
  }
}

variable "storage_class" {
  type        = string
  default     = ""
  description = "global.storageClass for RWO volumes. Empty keeps the cluster default. Do not point Delta Lake at a RWO class."

  validation {
    condition     = var.storage_class == "" || can(regex("^[A-Za-z0-9._-]+$", var.storage_class))
    error_message = "storage_class must be a storage class name."
  }
}

variable "delta_lake_storage_class" {
  type        = string
  default     = ""
  description = "deltaLake.persistence.storageClass. Must be ReadWriteMany. Empty uses storage_class when that is set, otherwise the chart default."

  validation {
    condition     = var.delta_lake_storage_class == "" || can(regex("^[A-Za-z0-9._-]+$", var.delta_lake_storage_class))
    error_message = "delta_lake_storage_class must be a storage class name."
  }
}
