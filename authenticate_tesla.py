"""
Interactive Tesla Authentication Utility
Authorizes TeslaPy using Tesla's OAuth SSO and saves credentials locally to cache.json.
This works in headless environments (like Alpine Proxmox LXC) without needing a desktop browser.
"""

import os
import sys
import yaml
import teslapy


def main():
    print("=" * 60)
    print(" Tesla Account Authorization for Solar Optimizer")
    print("=" * 60)

    # Try to read email from config.yaml if available
    email = None
    if os.path.exists("config.yaml"):
        try:
            with open("config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                email = cfg.get("tesla", {}).get("email")
        except Exception:
            pass

    if not email or email == "your-tesla-email@example.com":
        email = input("\nEnter your Tesla account email: ").strip()

    if not email:
        print("Error: Email is required.")
        sys.exit(1)

    print(f"\nInitializing Tesla authentication for: {email}")
    tesla = teslapy.Tesla(email)

    if tesla.authorized:
        print("\n✅ This account is already authorized!")
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
        state = tesla.new_state()
        code_verifier = tesla.new_code_verifier()
        auth_url = tesla.authorization_url(state=state, code_verifier=code_verifier)

        print("\n" + "-" * 60)
        print("STEP 1: Open the following URL in any browser on your computer or phone:")
        print("-" * 60)
        print(f"\n{auth_url}\n")
        print("-" * 60)
        print("STEP 2: Log in to your Tesla account on that page.")
        print("After logging in, you will be redirected to a page that says 'Page Not Found' (404).")
        print("THIS IS EXPECTED! Do not close the tab.")
        print("STEP 3: Copy the ENTIRE URL from your browser address bar.")
        print("-" * 60)

        redirect_url = input("\nPaste the full URL here: ").strip()

        print("\nVerifying token with Tesla...")
        tesla.fetch_token(authorization_response=redirect_url, state=state, code_verifier=code_verifier)

        if tesla.authorized:
            print("\n🎉 Authorization successful! Credentials saved to cache.json.")
            vehicles = tesla.vehicle_list()
            print(f"\nFound {len(vehicles)} vehicle(s) on your account:")
            for v in vehicles:
                print(f" - {v.get('display_name')} (VIN: {v.get('vin')}, State: {v.get('state')})")
        else:
            print("\n❌ Authorization failed. Please verify the URL pasted and try again.")

    except Exception as e:
        print(f"\n❌ Error during authorization: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
