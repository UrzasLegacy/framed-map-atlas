# Import selected map art into Minecraft Java 26.3

Extract the entire selection ZIP. Install Node.js 20 or newer. The importer runs locally and requires no npm packages.

1. In the destination world, create and use a vanilla map at least once so Minecraft saves its map counter. Save and stop the server or close the single-player world. Back up the world. Keep it closed until copying is complete.
2. From the extracted download, run the following, supplying the **existing shared map directory** containing `last_id.dat`, a fresh staging folder, and the recipient’s Java username.

   ```powershell
   node import-world.cjs "D:/MyWorld/data/minecraft/maps" "D:/MapImport-01" PlayerName
   ```

3. Review `mapping.json` and `commands.txt` in the staging folder. Copy **the contents of its maps folder** into that same destination map directory. Include the updated `last_id.dat` and `wubcraft-gallery-ids.json`. Keep the world’s existing numeric DATs. If a newly staged numeric DAT would overwrite an existing file, stop and regenerate from the current world in a new staging folder.
4. Start the world. Run each line of `commands.txt` in the server console while the named player is online. For a local world use an operator command block or a function with the appropriate command-length allowance. Large commands can exceed chat or host-console limits.

The importer allocates above the highest numeric filename and the world’s saved last ID. Later downloads reuse previously imported DATs whose bytes still match the ledger. If Minecraft has rewritten a DAT, the importer assigns a fresh ID and preserves the existing file. Stage and apply one selection at a time. The exporter includes each requested DAT once and preserves every named bundle and tile placement in the selection.

The commands copied directly from the gallery use Wubcraft’s IDs. For other worlds use the commands produced by this importer.

