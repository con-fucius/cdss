terraform {
  required_version = ">= 1.0"
  
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# VPC and Networking
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
  
  tags = {
    Name = "umls-cdss-vpc"
  }
}

# RDS PostgreSQL with pgvector
resource "aws_db_instance" "postgres" {
  identifier     = "umls-cdss-db"
  engine         = "postgres"
  engine_version = "16.1"
  instance_class = var.db_instance_class
  
  allocated_storage     = 100
  max_allocated_storage = 500
  storage_type          = "gp3"
  
  db_name  = "umls_cdss"
  username = var.db_username
  password = var.db_password
  
  vpc_security_group_ids = [aws_security_group.rds.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name
  
  backup_retention_period = 7
  skip_final_snapshot    = true
  
  tags = {
    Name = "umls-cdss-database"
  }
}

# ECS Cluster
resource "aws_ecs_cluster" "main" {
  name = "umls-cdss-cluster"
}

# TODO: Add ECS service, task definitions, load balancer, etc.

