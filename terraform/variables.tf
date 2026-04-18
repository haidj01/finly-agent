variable "aws_region" {
  default = "us-east-1"
}

variable "app_name" {
  default = "finly-agent"
}

variable "image_tag" {
  description = "ECR 이미지 태그 (CI/CD에서 주입)"
  default     = "latest"
}

variable "container_port" {
  default = 8001
}

variable "cpu" {
  default = "256"
}

variable "memory" {
  default = "512"
}
