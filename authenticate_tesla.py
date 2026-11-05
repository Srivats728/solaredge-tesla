"""
Interactive Tesla Authentication Utility
Authorizes vehicle access and saves credentials to cache.json and config.yaml.
Zero emojis, strict Tesla console output.
"""

import json
import os
import sys
import requests
import yaml
import teslapy


def main():
    print("=" * 60)
    print(" TESLA VEHICLE AUTHORIZATION UTILITY")
    print("=" * 60)

    # Try to read email from config.yaml if available
    email = None
    if os.path.exists("config.yaml"):
        try:
            with open("config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
                email = cfg.get("tesla", {}).get("email")
        except Exception:
            pass

    print("\nSelect authentication method:")
    print(" [1] Browser SSO Sign-In (Standard Tesla login link)")
    print(" [2] Paste Refresh Token (from Tesla Auth mobile/desktop app)")
    choice = input("\nEnter choice [1 or 2, default 1]: ").strip() or "1"

    if choice == "2":
        handle_refresh_token(email)
    else:
        handle_sso_login(email)


def handle_refresh_token(email=None):
    print("\n" + "-" * 60)
    print(" METHOD: Refresh Token Authentication")
    print("-" * 60)
    print("Open the free Tesla Auth app on iOS, Android, or PC/Mac,")
    print("sign in with your Tesla credentials, and copy your Refresh Token.")
    print("-" * 60)

    token = input("\nPaste Tesla Refresh Token: ").strip()
    if not token:
        print("[ERROR] Token cannot be empty.")
        sys.exit(1)

    print("\nExchanging refresh token with Tesla OAuth server...")
    try:
        resp = requests.post(
            "https://auth.tesla.com/oauth2/v3/token",
            json={
                "grant_type": "refresh_token",
                "client_id": "ownerapi",
                "refresh_token": token,
                "scope": "openid email offline_access"
            },
            timeout=15
        )
        if resp.status_code != 200:
            print(f"[ERROR] Tesla token exchange failed ({resp.status_code}): {resp.text}")
            sys.exit(1)

        tdata = resp.json()
        access_token = tdata.get("access_token")
        new_refresh_token = tdata.get("refresh_token", token)

        # Query user info to get email if not set
        if not email or email == "your-tesla-email@example.com":
            try:
                u_resp = requests.get(
                    "https://auth.tesla.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=8
                )
                if u_resp.status_code == 200:
                    email = u_resp.json().get("email")
            except Exception:
                pass

        if not email:
            email = input("Enter your Tesla account email: ").strip()

        # Test vehicle access
        print("Connecting to Tesla Owner API...")
        v_resp = requests.get(
            "https://owner-api.teslamotors.com/api/1/vehicles",
            headers={"Authorization": f"Bearer {access_token}", "User-Agent": "Tesla/4.30.0"},
            timeout=15
        )
        if v_resp.status_code != 200:
            print(f"[ERROR] Vehicle query failed ({v_resp.status_code}): {v_resp.text}")
            sys.exit(1)

        vehicles = v_resp.json().get("response", [])
        print(f"\n[SUCCESS] Connected! Found {len(vehicles)} vehicle(s):")
        for v in vehicles:
            print(f" - {v.get('display_name')} (VIN: {v.get('vin')}, State: {v.get('state')})")

        # Save to config.yaml
        cfg = {}
        if os.path.exists("config.yaml"):
            with open("config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

        if "tesla" not in cfg:
            cfg["tesla"] = {}
        cfg["tesla"]["provider"] = "fleet"
        cfg["tesla"]["email"] = email
        cfg["tesla"]["fleet"] = {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "client_id": "ownerapi",
            "region": "na"
        }
        with open("config.yaml", "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False)

        # Also write cache.json for teslapy interoperability
        if email:
            cache = {}
            if os.path.exists("cache.json"):
                try:
                    with open("cache.json", "r", encoding="utf-8") as cf:
                        cache = json.load(cf) or {}
                except Exception:
                    pass
            cache[email] = {
                "sso": {
                    "access_token": access_token,
                    "refresh_token": new_refresh_token,
                    "token_type": "Bearer",
                    "expires_in": 28800
                }
            }
            with open("cache.json", "w", encoding="utf-8") as cf:
                json.dump(cache, cf, indent=2)

        print("\n[OK] Configuration updated successfully. Restart solaredge-tesla to apply:")
        print("  systemctl restart solaredge-tesla")

    except Exception as e:
        print(f"[ERROR] Authentication error: {e}")
        sys.exit(1)


def handle_sso_login(email=None):
    if not email or email == "your-tesla-email@example.com":
        email = input("\nEnter your Tesla account email: ").strip()

    if not email:
        print("[ERROR] Email is required.")
        sys.exit(1)

    print(f"\nInitializing Tesla authentication for: {email}")
    tesla = teslapy.Tesla(email)

    if tesla.authorized:
        print("\n[OK] This account is already authorized.")
        confirm = input("Do you want to re-authenticate? (y/N): ").strip().lower()
        if confirm != 'y':
            print("\nTesting vehicle connection...")
            try:
                vehicles = tesla.vehicle_list()
                print(f"Found {len(vehicles)} vehicle(s):")
                for v in vehicles:
                    print(f" - {v.get('display_name')} (VIN: {v.get('vin')}, State: {v.get('state')})")
            except Exception as e:
                print(f"Error fetching vehicles: {e}")
            return

    # Headless OAuth PKCE flow
    try:
        raw_state = tesla.new_state()
        raw_verifier = tesla.new_code_verifier()
        verifier_bytes = raw_verifier if isinstance(raw_verifier, bytes) else raw_verifier.encode("utf-8")
        state_bytes = raw_state if isinstance(raw_state, bytes) else raw_state.encode("utf-8")

        auth_url = tesla.authorization_url(state=state_bytes, code_verifier=verifier_bytes)
        if isinstance(auth_url, bytes):
            auth_url = auth_url.decode("utf-8")

        print("\n" + "-" * 60)
        print("STEP 1: Open the following URL in any browser on your computer or phone:")
        print("-" * 60)
        print(f"\n{auth_url}\n")
        print("-" * 60)
        print("STEP 2: Log in to your Tesla account on that page.")
        print("After logging in, you will be redirected to a page that says 'Page Not Found' (404).")
        print("THIS IS EXPECTED. Do not close the tab.")
        print("STEP 3: Copy the ENTIRE URL from your browser address bar.")
        print("-" * 60)

        redirect_url = input("\nPaste the full URL here: ").strip()

        print("\nVerifying token with Tesla...")
        tesla.fetch_token(authorization_response=redirect_url, state=state_bytes, code_verifier=verifier_bytes)

        if tesla.authorized:
            print("\n[SUCCESS] Authorization successful! Credentials saved to cache.json.")
            vehicles = tesla.vehicle_list()
            print(f"\nFound {len(vehicles)} vehicle(s) on your account:")
            for v in vehicles:
                print(f" - {v.get('display_name')} (VIN: {v.get('vin')}, State: {v.get('state')})")

            # Update config.yaml with email and provider
            if os.path.exists("config.yaml"):
                with open("config.yaml", "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                if "tesla" not in cfg:
                    cfg["tesla"] = {}
                cfg["tesla"]["provider"] = "teslapy"
                cfg["tesla"]["email"] = email
                with open("config.yaml", "w", encoding="utf-8") as f:
                    yaml.dump(cfg, f, default_flow_style=False)

            print("\n[OK] Configuration updated. Restart solaredge-tesla to apply:")
            print("  systemctl restart solaredge-tesla")
        else:
            print("\n[FAILED] Authorization failed. Please verify the URL pasted and try again.")

    except Exception as e:
        print(f"\n[ERROR] Error during authorization: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
