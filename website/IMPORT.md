# Install gallery maps in Minecraft Java 26.3

**Gallery IDs start at 100,000.** A file named `100000.dat` supplies map #100000. Use the same gallery command on any world where you installed those DATs.

1. Extract the ZIP. Save and close the world or stop the server.
2. Copy the numbered files from `dats/` into the world's shared `data/minecraft/maps/` directory, keeping their filenames. Skip files already installed. If a filename is already taken by a different DAT, stop before overwriting it. The optional checker below can compare the files for you.
3. Start the world. Enter the recipient's username on the gallery and run the selected box commands in the server console.

Leave `last_id.dat` as it is. A world whose last issued map ID is 500 will issue 501 next, even after installing #100000. Vanilla can reuse the imported ID and overwrite its art if the normal map counter eventually reaches it.

## Optional checksum and collision check

With Node.js 20 or newer, run this from the extracted ZIP using the destination's existing map directory and a fresh output folder.

```powershell
node import-world.cjs "D:/MyWorld/data/minecraft/maps" "D:/MapImport-01" PlayerName
```

The checker compares each selected DAT against its download checksum and the target filename. Exact matches are skipped. A different file at the requested ID stops the check before staging anything. On success, copy the missing numeric DATs from the staged `maps/` folder and use `commands.txt` for the named player.
