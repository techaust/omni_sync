import { setNonce } from 'get-nonce'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'

import { AppProviders, makeQueryClient } from '@/app'
import { getCspNonce } from '@/lib/csp'
import { createRouter } from '@/router'
import './index.css'

// Libraries that insert <style> elements (Radix scroll-lock) read the nonce through get-nonce,
// so they satisfy the CSP without 'unsafe-inline' (C-22). The server injects it per request.
const nonce = getCspNonce()
if (nonce) setNonce(nonce)

const root = document.getElementById('root')
if (!root) throw new Error('missing #root element')

createRoot(root).render(
  <StrictMode>
    <AppProviders client={makeQueryClient()}>
      <RouterProvider router={createRouter()} />
    </AppProviders>
  </StrictMode>,
)
