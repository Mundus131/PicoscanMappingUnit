from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    debug: bool = True
    
    # API Info
    api_title: str = "Picoscan Mapping Unit API"
    api_version: str = "0.1.0"
    api_description: str = "API for Picoscan 3D mapping and point cloud processing"
    
    # Picoscan
    picoscan_devices_config: str = "config/picoscans_config.json"
    
    # Logging
    log_level: str = "INFO"
    log_file: str = "logs/app.log"
    
    # Database
    database_url: str = "sqlite:///./data/picoscan.db"
    
    # CORS
    cors_origins: List[str] = ["http://localhost:3000", "http://localhost:5173"]
    
    # Processing
    missing_data_interpolation: bool = True
    point_cloud_processing_timeout: int = 300
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        env_file_encoding = 'utf-8'


settings = Settings()
