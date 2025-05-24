import asyncio
import argparse
import logging
import os
import sys
import uvicorn

# Local imports
from core.server import server, create_application

# Configure basic console logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Set up detailed file logging
try:
    root_logger = logging.getLogger()
    log_file_dir = os.path.dirname(os.path.abspath(__file__))
    log_file_path = os.path.join(log_file_dir, 'mcp_server_debug.log')

    file_handler = logging.FileHandler(log_file_path, mode='a')
    file_handler.setLevel(logging.DEBUG)

    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(process)d - %(threadName)s '
        '[%(module)s.%(funcName)s:%(lineno)d] - %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    logger.info(f"Detailed file logging configured to: {log_file_path}")
except Exception as e:
    sys.stderr.write(f"CRITICAL: Failed to set up file logging to '{log_file_path}': {e}\n")

# Import calendar tools to register them with the MCP server via decorators
import gcalendar.calendar_tools
import gdrive.drive_tools
import gmail.gmail_tools
import gdocs.docs_tools


def main():
    """
    Main entry point for the Google Workspace MCP server.
    Uses streamable-http transport via a Starlette application with SessionAwareStreamableHTTPManager.
    """
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Google Workspace MCP Server')
    parser.add_argument('--single-user', action='store_true',
                        help='Run in single-user mode - bypass session mapping and use any credentials from ./credentials directory')
    args = parser.parse_args()

    # Set global single-user mode flag
    if args.single_user:
        os.environ['MCP_SINGLE_USER_MODE'] = '1'
        logger.info("Starting in single-user mode - bypassing session-to-OAuth mapping")

    try:
        logger.info("Google Workspace MCP server starting...")

        # Create the Starlette application with our custom session manager
        app = create_application(base_path="/mcp")

        # Run the application with uvicorn
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=int(os.getenv("WORKSPACE_MCP_PORT", 8000)),
            log_level="info"
        )
    except KeyboardInterrupt:
        logger.info("Server shutdown requested via keyboard interrupt")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error running server: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
