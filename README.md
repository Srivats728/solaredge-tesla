# SolarEdge ↔ Tesla Solar Self-Consumption Optimizer ☀️🚗

An automated, LAN-based solar charging manager for your **Tesla Model 3** powered directly by your **SolarEdge Inverter** over **Modbus TCP**.

Designed specifically for **Level 1 (120V)** charging without a grid consumption meter, maximizing free solar self-consumption while avoiding drawing power from the grid.

---

## ⚡ Features & How It Works

1. **Direct Local Modbus TCP**
   - Connects directly to the SolarEdge inverter at `192.168.40.169:1502` over your local network.
   - Zero reliance on the cloud SolarEdge API or API rate limits.
2. **Zero Vampire Drain Strategy**
   - When solar generation is low or at night, the script **never wakes the car**, allowing the car's computer to stay in deep sleep.
   - Only wakes the vehicle when sustained solar generation is confirmed.
3. **Dynamic Level 1 Current Modulation**
   - Continuously scales current between **5A and 12A** in real-time based on solar output.
   - Smoothly ramps down to 5A before pausing if clouds roll in.
4. **Cloud / Spike Hysteresis (Debouncing)**
   - Protects vehicle contactors and mobile charger relays by enforcing a start timer (`120s`) and stop timer (`180s`) to ignore temporary dips or brief spikes.
5. **WhatsApp Notifications**
   - Optional alerts via **CallMeBot** (free) or **Twilio** when charging starts, stops, or adjusts.
6. **Built for Proxmox LXC (Alpine Linux)**
   - Ultra-lightweight footprint (< 30 MB RAM) running as an OpenRC background service.

---

## 📐 Charging Logic & Math

Since there is no consumption meter ("C+"), the system allocates power using an estimated household baseline:

$$\text{Available Power for Car} = \text{Solar Generation} - \text{Household Baseline}$$

$$\text{Target Amps} = \left\lfloor \frac{\text{Available Power}}{120\text{ V}} \right\rfloor \quad (\text{clamped between } 5\text{A and } 12\text{A})$$

| Solar Output | Baseline House | Available for Car | Level 1 Current | Effective Car Power |
| :--- | :--- | :--- | :--- | :--- |
| **< 1,100 W** | 600 W | < 500 W | **0 A (Stopped)** | 0 W |
| **1,300 W** | 600 W | 700 W | **5 A** | 600 W |
| **1,500 W** | 600 W | 900 W | **7 A** | 840 W |
| **1,800 W** | 600 W | 1,200 W | **10 A** | 1,200 W |
| **≥ 2,100 W** | 600 W | ≥ 1,500 W | **12 A (Max L1)** | 1,440 W |

- **Start Threshold**: Generation must stay **> 1,700 W** for **120 seconds** before waking the car and starting charging.
- **Stop Threshold**: If generation drops **< 1,100 W**, amps immediately drop to 5A. If it stays under 1,100 W for **180 seconds**, charging stops and the car is allowed to sleep.

---

## 🚀 Quick Setup on Proxmox LXC (Alpine Linux)

### 1. Create the Alpine LXC in Proxmox
- In Proxmox VE: Click **Create CT**.
- Select the **Alpine Linux** template (e.g. `alpine-3.19` or `alpine-3.20`).
- Resources needed: **512 MB RAM**, **1 vCPU**, **2 GB Disk** (extremely lightweight).
- Network: Bridge to your local LAN so it can reach `192.168.40.169`.

### 2. Install the Service
Open the Proxmox LXC Console (or SSH into it as `root`):

```sh
# Clone or copy this repository to the LXC:
apk add git
git clone <your-repo-url> /opt/solaredge-tesla
cd /opt/solaredge-tesla

# Run the automated installer:
sh deploy/install_alpine.sh
```

---

## 🔑 Configuration & First-Time Setup

### 1. Update `config.yaml`
```sh
nano /opt/solaredge-tesla/config.yaml
```
Enter your **Tesla account email**:
```yaml
tesla:
  email: "your-email@example.com"
```
*(Optionally enable WhatsApp alerts by setting `whatsapp.enabled: true` and entering your CallMeBot API key).*

---

### 2. Authenticate Your Tesla Account
Tesla uses OAuth with PKCE. To authorize on a headless server without a desktop browser:

```sh
/opt/solaredge-tesla/venv/bin/python /opt/solaredge-tesla/authenticate_tesla.py
```
1. The script will print a Tesla login URL.
2. Open that URL on your computer or phone browser and log in with your Tesla credentials.
3. Once logged in, your browser will land on a **"Page Not Found" (404)** page. **This is expected!**
4. Copy the entire URL from the browser's address bar, paste it back into the terminal prompt, and press Enter.
5. Your tokens are securely saved to `cache.json` and will automatically refresh in the background.

---

### 3. Test the SolarEdge Inverter Modbus Connection
Once your Wi-Fi antenna is connected and inverter is on:

```sh
/opt/solaredge-tesla/venv/bin/python /opt/solaredge-tesla/test_modbus.py
```
This utility checks:
- Network ping & Modbus TCP handshake (`192.168.40.169:1502`)
- SunSpec base register auto-detection
- Real-time generation readout in Watts / kW

---

### 4. Start the Background Service
Enable the OpenRC service so it starts automatically on boot:

```sh
# Add to boot runlevel:
rc-update add solaredge-tesla default

# Start service now:
rc-service solaredge-tesla start

# Check service status:
rc-service solaredge-tesla status

# View live real-time logs:
tail -f /var/log/solaredge-tesla.log
```

---

## 📱 WhatsApp Notifications Setup (CallMeBot)

If you'd like free WhatsApp alerts when charging starts, stops, or adjusts:
1. Add the phone number **`+34 910 60 41 44`** to your phone's WhatsApp contacts (name it "CallMeBot").
2. Send the message: **`I allow callmebot to send me messages`** to that contact in WhatsApp.
3. Wait ~1-2 minutes for the bot to reply with your unique **API Key**.
4. In `config.yaml`, set:
   ```yaml
   whatsapp:
     enabled: true
     provider: "callmebot"
     callmebot:
       phone: "+1XXXXXXXXXX"  # Your phone number with country code
       apikey: "1234567"      # The API key received
   ```

---

## 🛠️ SolarEdge Inverter Configuration Checklist

When you install the Wi-Fi antenna:
1. Connect to the inverter using the **SolarEdge SetApp** mobile app (or front LCD menu on older models).
2. Go to **Communication** → **RS485** or **LAN / Wi-Fi**.
3. Locate **Modbus TCP**:
   - Status: **Enabled**
   - Port: **1502** (default)
4. Verify the inverter IP is `192.168.40.169` (consider reserving this IP in your router's DHCP settings).
> **Note**: SolarEdge allows only **one active Modbus TCP connection** at a time. Ensure no other third-party system holds port 1502 open.

---

## 📂 Repository File Structure

```
solaredge-tesla/
├── config.yaml               # Active user settings & parameters
├── config.example.yaml       # Template configuration with descriptions
├── requirements.txt          # Python dependencies (teslapy, pymodbus, etc.)
├── main.py                   # Main daemon loop and coordinator
├── solaredge.py              # SunSpec Modbus TCP client for SolarEdge
├── tesla_client.py           # TeslaPy wrapper with anti-vampire-drain logic
├── controller.py             # Charging algorithm, debouncing & dynamic amps
├── notifier.py               # WhatsApp alert handler (CallMeBot / Twilio)
├── test_modbus.py            # Modbus connection tester CLI
├── authenticate_tesla.py     # Interactive headless Tesla OAuth login
├── deploy/
│   ├── install_alpine.sh     # One-step Alpine LXC installer
│   ├── solaredge-tesla.initd # OpenRC service script (/etc/init.d/...)
│   ├── Dockerfile            # Container definition
│   └── docker-compose.yml    # Docker Compose definition
└── README.md
```
