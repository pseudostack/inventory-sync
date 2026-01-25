
#!/bin/bash
set -e
cd /root/inventory-sync/backend
exec /root/inventory-sync/backend/venv/bin/python3 -u /root/inventory-sync/backend/update_inventory.py
