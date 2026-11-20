import { useState } from 'react'
import { Check, Info, Save } from 'lucide-react'
import { readUserPreferences, saveDisplayNamePreference } from '../userPreferences'

export default function SettingsPage({ ownerId, email, onDisplayNameChange }: { ownerId: string; email: string; onDisplayNameChange: (name: string) => void }) {
  const [name, setName] = useState(() => readUserPreferences(ownerId).displayName)
  const [saved, setSaved] = useState(false)
  function save() {
    const next = saveDisplayNamePreference(ownerId, name)
    setName(next.displayName); onDisplayNameChange(next.displayName); setSaved(true)
  }
  return <div className="utility-page settings-page"><span className="eyebrow"><span className="eyebrow-line" /> ACCOUNT PREFERENCES</span><h1>Settings</h1><p>Manage display preferences for this account in this browser.</p>
    <section className="settings-card"><h2>Display name</h2><p>Shown in your SkillFair workspace on this browser. Your account email and server-side account details are unchanged.</p><label htmlFor="display-name">Name shown in the workspace</label><input id="display-name" value={name} maxLength={50} onChange={(event) => { setName(event.target.value); setSaved(false) }} placeholder={email.split('@')[0]} /><button type="button" className="button-primary" onClick={save}><Save size={15} />Save on this browser</button>{saved && <p className="settings-saved" role="status"><Check size={15} /> Display preference saved locally.</p>}</section>
    <section className="settings-note"><Info size={17} /><div><b>Settings availability</b><p>The display name is stored in this browser, scoped to your signed-in account. Theme and notification preferences are not currently supported and are not presented as active controls. Password recovery, multi-factor authentication, and email verification are not available in this demo.</p></div></section>
  </div>
}
