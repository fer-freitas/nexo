variable "environment" { type = string }
variable "region" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "internal_cidrs" { type = list(string) }
variable "certificate_arn" { type = string }
variable "image" { type = string }
variable "public_url" { type = string }
variable "alarm_topic_arns" {
  type    = list(string)
  default = []
}
# Ponto de partida, sem medição: ajustar com carga real.
variable "task_cpu" {
  type    = number
  default = 512
}
variable "task_memory" {
  type    = number
  default = 1024
}
variable "desired_count" {
  type    = number
  default = 2
}
variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}
