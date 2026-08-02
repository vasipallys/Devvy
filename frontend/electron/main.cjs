const { app, BrowserWindow, dialog, ipcMain, session, shell } = require('electron')
const path = require('path')

const isDev = !app.isPackaged
let mainWindow

function createWindow() {
  const window = new BrowserWindow({
    width: 1440, height: 920, minWidth: 900, minHeight: 620,
    backgroundColor: '#0b0d0e', titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true, sandbox: true, nodeIntegration: false,
    },
  })
  mainWindow = window
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url)
    return { action: 'deny' }
  })
  if (isDev) window.loadURL('http://localhost:5173')
  else window.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
}

ipcMain.handle('desktop:pick-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Select code workspace', properties: ['openDirectory'],
  })
  return result.canceled ? null : result.filePaths[0]
})
ipcMain.handle('desktop:pick-files', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Select target source files', properties: ['openFile', 'multiSelections'],
  })
  return result.canceled ? [] : result.filePaths
})
ipcMain.handle('desktop:reveal', (_event, target) => {
  if (typeof target === 'string' && target) shell.showItemInFolder(target)
})

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => {
    callback(permission === 'media')
  })
  createWindow()
  app.on('activate', () => { if (!BrowserWindow.getAllWindows().length) createWindow() })
})
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
