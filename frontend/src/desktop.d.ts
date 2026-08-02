export {}

declare global {
  interface Window {
    desktop?: {
      platform: string
      versions: { electron: string }
      pickFolder: () => Promise<string | null>
      pickFiles: () => Promise<string[]>
      reveal: (path: string) => Promise<void>
    }
  }
}
