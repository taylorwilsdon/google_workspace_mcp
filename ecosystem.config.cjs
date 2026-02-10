module.exports = {
  apps: [
    {
      // Google Workspace MCP Server - HTTP/Streamable mode
      name: 'mcp-google-workspace',
      script: 'uv',
      args: 'run python main.py --transport streamable-http --single-user --ssl-cert ./certs/localhost+2.pem --ssl-key ./certs/localhost+2-key.pem',
      cwd: '/Users/robsherman/Servers/mcp-google-workspace',
      instances: 1,
      exec_mode: 'fork',
      watch: false,
      env: {
        NODE_ENV: 'production',
        PORT: '8103',
        WORKSPACE_MCP_BASE_URI: 'https://localhost',
        PYTHONPATH: '/Users/robsherman/Servers/mcp-google-workspace',
      },
      env_development: {
        NODE_ENV: 'development',
      },
      // Logging configuration
      log_file: './logs/mcp-google-workspace.log',
      error_file: './logs/mcp-google-workspace-error.log',
      out_file: './logs/mcp-google-workspace-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,

      // Auto-restart configuration
      autorestart: true,
      restart_delay: 4000,
      max_restarts: 10,
      min_uptime: '10s',

      // Memory and CPU limits
      max_memory_restart: '500M',

      // Process management
      kill_timeout: 5000,
      listen_timeout: 8000,

      // Error handling
      exp_backoff_restart_delay: 100
    }
  ]
};
