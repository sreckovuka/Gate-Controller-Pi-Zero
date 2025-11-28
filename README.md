PiZero Gate controller With BLYNK for mobile app control to open gate and monitor reed switch if gate is open os closed.
Web intface on port 5000 to set time schedule to open and close, Logic to read the read switch if gate is open dont open the gate on schedule 
and if the gate is close dont close the gate on schedule to prevent closing of opening the gate in error
Schedule can be disable via the web page also has open and close function as set time for schedle to open and close the gate.
The relay is pulsed on for 1 second to open and 1 second to close a gate.
blynk.log_event send events to blynk, set via automation to alert of open / close on web app.
timezone and wifi set via raspi-config in termial or when writing the flashcard.
setup crontab -e or service to run on boot.

sudo nano /etc/systemd/system/gatecontroller.service
add these lines
[Unit]
Description=Pi Gate Controller v4.0
After=network.target

[Service]
Type=simple
User=pi   change to user user login
WorkingDirectory=/home/pi/gatecontroller
ExecStart=/usr/bin/python3 /home/pi/gatecontroller/gatecontroller.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target

to save and exit
cntl x y

to enable server and check
sudo systemctl daemon-reload
sudo systemctl enable gatecontroller.service
sudo systemctl start gatecontroller.service
sudo systemctl status gatecontroller.service


