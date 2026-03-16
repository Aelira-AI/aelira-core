#!/usr/bin/env python3
"""
Analyze all API endpoints to identify which ones lack authentication.
"""

import os
import re
from pathlib import Path
from collections import defaultdict

def analyze_route_file(filepath):
    """Analyze a single route file for endpoint definitions and auth status."""
    with open(filepath, 'r') as f:
        content = f.read()
        lines = content.split('\n')

    # Find all route decorators
    route_pattern = r'@router\.(get|post|put|delete|patch)\(["\']([^"\']+)'
    auth_patterns = [
        r'Depends\(get_current_api_key\)',
        r'api_key:\s*APIKey\s*=\s*Depends\(get_current_api_key\)',
        r'current_key:\s*APIKey\s*=\s*Depends\(get_current_api_key\)',
    ]

    endpoints = []

    for i, line in enumerate(lines):
        match = re.search(route_pattern, line)
        if match:
            method = match.group(1).upper()
            path = match.group(2)

            # Look at the next 10 lines for authentication
            has_auth = False
            function_def = ""
            for j in range(i, min(i + 15, len(lines))):
                if 'def ' in lines[j]:
                    function_def = lines[j]
                for auth_pattern in auth_patterns:
                    if re.search(auth_pattern, lines[j]):
                        has_auth = True
                        break
                if has_auth:
                    break

            # Also check for optional auth patterns
            optional_auth = False
            if 'get_api_key_or_mock' in function_def or '_get_optional_api_key' in function_def:
                optional_auth = True

            endpoints.append({
                'method': method,
                'path': path,
                'has_auth': has_auth,
                'optional_auth': optional_auth,
                'line': i + 1,
                'function': function_def.strip()
            })

    return endpoints

def main():
    api_dir = Path('src/api')

    all_endpoints = defaultdict(list)
    authenticated = []
    optional_auth = []
    unauthenticated = []

    # Analyze each route file
    for filepath in sorted(api_dir.glob('*_routes.py')):
        if '__pycache__' in str(filepath):
            continue

        filename = filepath.name
        endpoints = analyze_route_file(filepath)
        all_endpoints[filename] = endpoints

        for endpoint in endpoints:
            full_path = f"{endpoint['method']} {endpoint['path']}"
            if endpoint['has_auth']:
                authenticated.append((filename, full_path, endpoint['line']))
            elif endpoint['optional_auth']:
                optional_auth.append((filename, full_path, endpoint['line']))
            else:
                unauthenticated.append((filename, full_path, endpoint['line']))

    # Also analyze main.py
    main_file = Path('src/api/main.py')
    if main_file.exists():
        endpoints = analyze_route_file(main_file)
        all_endpoints['main.py'] = endpoints
        for endpoint in endpoints:
            full_path = f"{endpoint['method']} {endpoint['path']}"
            if endpoint['has_auth']:
                authenticated.append(('main.py', full_path, endpoint['line']))
            elif endpoint['optional_auth']:
                optional_auth.append(('main.py', full_path, endpoint['line']))
            else:
                unauthenticated.append(('main.py', full_path, endpoint['line']))

    # Print summary
    print("=" * 80)
    print("API ENDPOINT SECURITY AUDIT")
    print("=" * 80)
    print()

    total = len(authenticated) + len(optional_auth) + len(unauthenticated)
    print(f"Total endpoints: {total}")
    print(f"  ✅ Authenticated: {len(authenticated)} ({len(authenticated)/total*100:.1f}%)")
    print(f"  ⚠️  Optional auth: {len(optional_auth)} ({len(optional_auth)/total*100:.1f}%)")
    print(f"  ❌ Unauthenticated: {len(unauthenticated)} ({len(unauthenticated)/total*100:.1f}%)")
    print()

    # Print unauthenticated endpoints by category
    print("=" * 80)
    print("UNAUTHENTICATED ENDPOINTS")
    print("=" * 80)
    print()

    # Group by file
    by_file = defaultdict(list)
    for filename, endpoint, line in unauthenticated:
        by_file[filename].append((endpoint, line))

    for filename in sorted(by_file.keys()):
        print(f"\n{filename}:")
        for endpoint, line in sorted(by_file[filename]):
            print(f"  Line {line:4d}: {endpoint}")

    # Print optional auth endpoints
    if optional_auth:
        print()
        print("=" * 80)
        print("OPTIONAL AUTHENTICATION (Development fallback)")
        print("=" * 80)
        print()

        by_file = defaultdict(list)
        for filename, endpoint, line in optional_auth:
            by_file[filename].append((endpoint, line))

        for filename in sorted(by_file.keys()):
            print(f"\n{filename}:")
            for endpoint, line in sorted(by_file[filename]):
                print(f"  Line {line:4d}: {endpoint}")

if __name__ == '__main__':
    main()
