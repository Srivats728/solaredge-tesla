# SolarEdge // Tesla Solar Self-Consumption Optimizer

An ultra-minimalist, autonomous solar charging system for your **Tesla Model 3** powered directly by your **SolarEdge Inverter** over **Modbus TCP**.

Designed specifically for **Level 1 (120V)** charging with a **Tesla Mobile Connector Gen 1** plugged into a standard NEMA 5-15 wall socket without any smart plug or consumption meter. The charging current is dynamically adjusted inside the car itself between **5A and 12A** to track free solar surplus and avoid grid draw.

---

## 100% Free Vehicle Control (Zero Paid Subscriptions)

This software does **not** require Tessie or any paid third-party service. It provides three 100% free ways to command your Model 3:

| Method | Where It Runs | Internet Required? | Setup Time | Cost |
| :--- | :--- | :--- | :--- | :--- |
| **1. Local Bluetooth (BLE)** | Direct to car over Bluetooth | **No** (100% Offline) | 2 minutes | **$0.00** |
| **2. Official Fleet API** | Over the Internet (LTE/Wi-Fi) | Yes (Tesla Cloud) | 5 minutes | **$0.00** (Free $16/mo credit) |
| **3. Home Assistant Bridge** | Over local LAN to Home Assistant | LAN only | 1 minute | **$0.00** |

---

## Method 1: Local Bluetooth Low Energy (BLE) — Zero Cloud & 100% Offline

If your Proxmox server, Debian container, or Raspberry Pi is within Bluetooth range of your car (~30–40 ft / 10 m in the driveway or garage):

### How it works:
- Uses Tesla's official open-source tool: `tesla-control` ([github.com/teslamotors/vehicle-command](https://github.com/teslamotors/vehicle-command)).
- Communicates directly with the car's onboard Bluetooth receiver.
- **No Tesla developer account, no domain registration, no tokens, and zero cloud dependency.**

### Steps:
1. In the Web Dashboard (`http://<LXC-IP>:8080`), select **Bluetooth Low Energy (BLE)**.
2. Enter your car's **VIN** and click **Generate Virtual Key** (creates `private-key.pem`).
3. Pair the key to your Model 3 once by running this in your terminal:
   ```bash
   tesla-control -vin <YOUR_VIN> -ble add-key-request private-key.pem owner
   ```
4. Look at your Model 3's center touchscreen: it will prompt **"Key card required to authorize key"**. Tap your physical Tesla Key Card once on the center console.
5. Click **Save Vehicle** and **Test Connection** in the dashboard. The system now commands your car directly over Bluetooth for free!

---

## Method 2: Official Tesla Fleet API — 100% Free Cloud Control

If your server is located far from your car (e.g., in a basement or server rack) and cannot reach Bluetooth:

Tesla provides **$16/month in free API credits** to all developer accounts, which covers personal smart charging routines with zero out-of-pocket costs.

### Why Fleet API previously gave "HTTP 412" or "HTTP 403":
- **HTTP 412** happens because Tesla requires your developer application to be registered in your region (`POST /api/1/partner_accounts`) with a domain hosting `com.tesla.3p.public-key.pem`.
- **HTTP 403** happens because starting in 2024, vehicle commands (`charging-start`, `charging-set-amps`) must be cryptographically signed with your virtual key.

### Free Step-by-Step Setup:
1. **Free Public Key Hosting via GitHub Pages or FleetKey.cc**:
   - In this repository, `.well-known/appspecific/com.tesla.3p.public-key.pem` is already generated.
   - Simply enable **GitHub Pages** on your repository (`Settings` -> `Pages` -> `Deploy from branch main` -> `Save`).
   - Your free HTTPS public key is now live at:
     ```
     https://srivats728.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem
     ```
   - *(Alternative: You can also use [fleetkey.cc](https://fleetkey.cc) for free 1-click key hosting).*
2. **Register Partner Account (Eliminates HTTP 412)**:
   - On the Web Dashboard, enter your **Client ID**, **Client Secret**, and domain (`srivats728.github.io`).
   - Click **Register Partner Domain**. The dashboard automatically authenticates with Tesla and registers your domain. HTTP 412 is cleared permanently!
3. **Enroll Virtual Key in Car (1-Tap)**:
   - Open this official Tesla enrollment link on your phone:
     ```
     https://tesla.com/_ak/srivats728.github.io
     ```
   - The official Tesla mobile app will open, asking: *"Allow third-party access for srivats728.github.io?"*. Tap **Allow** and tap your key card on the console.
4. **Enter Your Tokens & Start**:
   - Paste your **Refresh Token** into the dashboard and click **Save Vehicle**.
   - Commands are signed using your private key and live telemetry streams directly to the dashboard!

---

## Method 3: Home Assistant Direct Bridge

> **Important Clarification**: Home Assistant's Tesla integration controls the **Model 3's internal onboard charger** directly over software. It does **NOT** require a smart plug or smart wall connector! It commands your car directly even when plugged into a dumb Mobile Connector Gen 1.

If you already run Home Assistant at home:
1. In the Web Dashboard, choose **Home Assistant**.
2. Enter your **HA Local URL** (e.g. `http://192.168.40.50:8123`) and a **Long-Lived Access Token** (created from your HA Profile page).
3. The optimizer will read battery % and adjust charging amps (5A–12A) through your local Home Assistant instance for free.

---

## 1-Step Update (Inside Debian LXC)

In your container terminal:

```bash
cd /opt/solaredge-tesla && git pull && systemctl restart solaredge-tesla
```

Open the Web Dashboard at:
```
http://<LXC-IP>:8080
```

---

## Charging Logic & Math

$$\text{Available Power for Car} = \text{Solar Generation} - \text{Household Baseline}$$

$$\text{Target Amps} = \left\lfloor \frac{\text{Available Power}}{120\text{ V}} \right\rfloor \quad (\text{clamped between } 5\text{A and } 12\text{A})$$

| Solar Output | Baseline House | Available for Car | Level 1 Current | Effective Car Power |
| :--- | :--- | :--- | :--- | :--- |
| **< 1,100 W** | 600 W | < 500 W | **0 A (Stopped)** | 0 W |
| **1,300 W** | 600 W | 700 W | **5 A** | 600 W |
| **1,500 W** | 600 W | 900 W | **7 A** | 840 W |
| **1,800 W** | 600 W | 1,200 W | **10 A** | 1,200 W |
| **>= 2,100 W** | 600 W | >= 1,500 W | **12 A (Max L1)** | 1,440 W |

- **Start Threshold**: Generation must stay **>= 1,700 W** for **120 seconds** before waking vehicle to charge.
- **Stop Threshold**: If generation drops **< 1,100 W**, current immediately steps down to 5A. If it stays below 1,100 W for **180 seconds** (cloud debounce buffer), charging stops and vehicle enters deep sleep.
- **Anti-Vampire-Drain**: When solar is low or during the night, the car is **never woken up**.

---

## Automated Test Suite

To verify all components locally:

```bash
pytest tests/ -v
```

All 24 unit, integration, and day-in-the-life lifecycle tests will pass in under 4 seconds.
