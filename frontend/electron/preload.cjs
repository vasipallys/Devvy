const { contextBridge, ipcRenderer } = require('electron')
contextBridge.exposeInMainWorld('desktop', {
  platform: process.platform,
  versions: { electron: process.versions.electron },
  pickFolder: () => ipcRenderer.invoke('desktop:pick-folder'),
  pickFiles: () => ipcRenderer.invoke('desktop:pick-files'),
  reveal: (path) => ipcRenderer.invoke('desktop:reveal', path),
})
