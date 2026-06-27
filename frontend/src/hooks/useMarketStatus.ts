import { useState, useEffect } from 'react'

export interface MarketStatus {
  isOpen: boolean
  minutesUntilClose: number | null  // set when open
  minutesUntilOpen: number | null   // set when closed
}

function computeStatus(): MarketStatus {
  const now = new Date()
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const wd = et.getDay() // 0=Sun, 6=Sat
  const mins = et.getHours() * 60 + et.getMinutes()

  const openMins = 9 * 60 + 30   // 9:30 AM
  const closeMins = 16 * 60       // 4:00 PM

  if (wd === 0 || wd === 6 || mins < openMins || mins >= closeMins) {
    // Market is closed — compute minutes until next open
    let minsUntilOpen: number
    if (wd === 0) {
      // Sunday: next open is Monday at 9:30
      minsUntilOpen = (24 * 60 - mins + openMins) + (0 * 24 * 60) // remaining today + Mon offset 0
      // Actually simpler: minutes until midnight + Monday's 9:30
      minsUntilOpen = (24 * 60 - mins) + openMins
    } else if (wd === 6) {
      // Saturday: next open is Monday
      minsUntilOpen = (24 * 60 - mins) + 24 * 60 + openMins
    } else if (mins < openMins) {
      // Weekday pre-market
      minsUntilOpen = openMins - mins
    } else {
      // Weekday after close — next open is next business day
      const daysUntilOpen = wd === 5 ? 3 : 1 // Friday → Monday
      minsUntilOpen = (24 * 60 - mins) + (daysUntilOpen - 1) * 24 * 60 + openMins
    }
    return { isOpen: false, minutesUntilClose: null, minutesUntilOpen: minsUntilOpen }
  }

  return { isOpen: true, minutesUntilClose: closeMins - mins, minutesUntilOpen: null }
}

export function useMarketStatus(): MarketStatus {
  const [status, setStatus] = useState<MarketStatus>(computeStatus)

  useEffect(() => {
    const id = setInterval(() => setStatus(computeStatus()), 30_000)
    return () => clearInterval(id)
  }, [])

  return status
}
