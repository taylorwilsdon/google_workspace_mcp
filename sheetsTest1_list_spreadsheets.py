#!/usr/bin/env python3
"""
Simple MCP client to test Google Sheets tools.
Uses FastMCP's Client class to connect to the running MCP server.

Usage:
    python list_spreadsheets.py <your-email@gmail.com>

Example:
    python list_spreadsheets.py robert.smith97879@gmail.com
"""

import asyncio
import sys
from fastmcp import Client


async def test_sheets_tools(user_email: str):
    """Test Google Sheets tools using FastMCP Client."""
    
    print("\n" + "="*70)
    print("Google Sheets MCP Client Test")
    print("="*70)
    print(f"Email: {user_email}")
    print(f"Server: http://localhost:8000/mcp")
    print("="*70 + "\n")
    
    # Connect to the MCP server via SSE transport
    async with Client("http://localhost:8000/mcp") as client:
        
        # Test: List spreadsheets
        print("\n" + "="*70)
        print("TEST: List Spreadsheets")
        print("="*70)
        try:
            result = await client.call_tool("list_spreadsheets", {
                "user_google_email": user_email
            })
            print("Success!")
            for content in result.content:
                print(content.text)
        except Exception as e:
            print(f"Error: {e}")
            if "Authorization URL" in str(e) or "ACTION REQUIRED" in str(e):
                print("\nAuthentication required. Check the server output for the authorization URL.")
                return
        
    print("\n" + "="*70)
    print("Test suite completed!")
    print("="*70 + "\n")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Error: Please provide your Google email address")
        print(f"\nUsage: python {sys.argv[0]} <your-email@gmail.com>")
        print("\nExample:")
        print(f"  python {sys.argv[0]} robert.smith97879@gmail.com")
        sys.exit(1)
    
    user_email = sys.argv[1]
    
    # Run the async test function
    asyncio.run(test_sheets_tools(user_email))


if __name__ == "__main__":
    main()
