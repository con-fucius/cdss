#!/bin/bash
# Reset pgAdmin master password

echo "Resetting pgAdmin configuration..."
echo "This will clear your saved connections and master password."

# Backup existing config
if [ -d ~/Library/Application\ Support/pgadmin ]; then
    BACKUP_DIR=~/Library/Application\ Support/pgadmin.backup.$(date +%Y%m%d_%H%M%S)
    echo "Backing up to: $BACKUP_DIR"
    cp -r ~/Library/Application\ Support/pgadmin "$BACKUP_DIR"
fi

# Remove session data (this forces pgAdmin to ask for new master password)
rm -rf ~/Library/Application\ Support/pgadmin/sessions 2>/dev/null
rm -rf ~/Library/Application\ Support/pgadmin/storage 2>/dev/null

echo "Done! Restart pgAdmin and set a new master password."
