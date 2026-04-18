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
