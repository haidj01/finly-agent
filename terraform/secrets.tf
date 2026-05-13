# API 키는 Terraform이 시크릿 리소스만 생성.
# 실제 값은 콘솔 또는 aws CLI로 별도 입력:
#   aws secretsmanager put-secret-value --secret-id finly/CLAUDE_API_KEY --secret-string "sk-ant-..."

resource "aws_secretsmanager_secret" "claude_api_key" {
  name                    = "finly/CLAUDE_API_KEY"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "alpaca_api_key" {
  name                    = "finly/ALPACA_API_KEY"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "alpaca_api_secret" {
  name                    = "finly/ALPACA_API_SECRET"
  recovery_window_in_days = 0
}

# Live trading credentials — already registered manually in Secrets Manager.
# If recreating from scratch: terraform import aws_secretsmanager_secret.alpaca_live_key finly/ALPACA_LIVE_KEY
resource "aws_secretsmanager_secret" "alpaca_live_key" {
  name                    = "finly/ALPACA_LIVE_KEY"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "alpaca_live_secret" {
  name                    = "finly/ALPACA_LIVE_SECRET"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "alpaca_mode" {
  name                    = "finly/ALPACA_MODE"
  recovery_window_in_days = 0
}
