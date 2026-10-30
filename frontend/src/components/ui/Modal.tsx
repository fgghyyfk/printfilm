import { createContext, useContext, useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useI18n } from '../../i18n'

type ModalProps = {
  open: boolean
  onClose: () => void
  title?: string
  children: ReactNode
  /** wider preview / form shells */
  size?: 'md' | 'lg' | 'xl'
  /** center dialog (default) or right-side drawer */
  variant?: 'center' | 'drawer'
  /** prevent closing via backdrop / Escape while busy */
  dismissible?: boolean
  footer?: ReactNode
  className?: string
}

type ModalEntry = {
  id: string
  parentId: string | null
  root: HTMLDivElement
  panel: HTMLDivElement
}

const ModalParentContext = createContext<string | null>(null)

// Nested dialogs share one body lock. Removing a parent must not unlock an open child.
const modalStack: ModalEntry[] = []
let previousBodyOverflow = ''

function isTopModal(entry: ModalEntry | null): boolean {
  return entry !== null && modalStack[modalStack.length - 1] === entry
}

function updateModalLayers() {
  modalStack.forEach((entry, index) => {
    const top = index === modalStack.length - 1
    entry.root.style.zIndex = String(1000 + index * 2)
    entry.root.inert = !top
    if (top) entry.root.removeAttribute('aria-hidden')
    else entry.root.setAttribute('aria-hidden', 'true')
    entry.panel.setAttribute('aria-modal', String(top))
  })
}

function focusableElements(panel: HTMLElement): HTMLElement[] {
  return Array.from(panel.querySelectorAll<HTMLElement>(
    'a[href], area[href], button, input, select, textarea, iframe, [contenteditable="true"], [tabindex]',
  )).filter((element) => (
    element.tabIndex >= 0
    && !element.matches(':disabled')
    && !element.closest('[hidden], [inert]')
    && element.getClientRects().length > 0
    && getComputedStyle(element).visibility !== 'hidden'
  ))
}

function focusInitialElement(panel: HTMLElement, preferredFocus?: HTMLElement | null) {
  // React's autoFocus runs during commit; preserve it when a form requested focus.
  if (panel.contains(document.activeElement)) return
  const elements = focusableElements(panel)
  const preferred = preferredFocus && elements.includes(preferredFocus)
    ? preferredFocus
    : elements.find((element) => element.matches('[autofocus], [data-autofocus]'))
  const target = preferred || elements[0] || panel
  target.focus({ preventScroll: true })
}

// 全局居中/抽屉弹层（portal 到 body，统一 pf-modal 样式）
export default function Modal({
  open,
  onClose,
  title,
  children,
  size = 'md',
  variant = 'center',
  dismissible = true,
  footer,
  className = '',
}: ModalProps) {
  const { t } = useI18n()
  const titleId = useId()
  const parentId = useContext(ModalParentContext)
  const rootRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const entryRef = useRef<ModalEntry | null>(null)
  const onCloseRef = useRef(onClose)
  const dismissibleRef = useRef(dismissible)
  const restoreFocusRef = useRef<HTMLElement | null>(null)
  const lastFocusedRef = useRef<HTMLElement | null>(null)
  const wasOpenRef = useRef(false)
  onCloseRef.current = onClose
  dismissibleRef.current = dismissible
  // Capture before portal children mount: their autoFocus may run before our effect.
  if (open && !wasOpenRef.current) {
    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    lastFocusedRef.current = null
  }
  wasOpenRef.current = open

  useEffect(() => {
    if (!open || !rootRef.current || !panelRef.current) return
    const entry = { id: titleId, parentId, root: rootRef.current, panel: panelRef.current }
    const restoreFocus = restoreFocusRef.current
    entryRef.current = entry
    if (modalStack.length === 0) previousBodyOverflow = document.body.style.overflow
    // Child effects run first on mount. Insert the parent below an already mounted child.
    const childIndex = modalStack.findIndex((item) => item.parentId === entry.id)
    if (childIndex === -1) modalStack.push(entry)
    else modalStack.splice(childIndex, 0, entry)
    document.body.style.overflow = 'hidden'
    updateModalLayers()
    if (isTopModal(entry)) focusInitialElement(entry.panel, lastFocusedRef.current)

    const onKey = (e: KeyboardEvent) => {
      if (!isTopModal(entry) || e.defaultPrevented) return
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        if (dismissibleRef.current) onCloseRef.current()
        return
      }
      if (e.key !== 'Tab') return
      const elements = focusableElements(entry.panel)
      const first = elements[0]
      const last = elements[elements.length - 1]
      if (!first) {
        e.preventDefault()
        entry.panel.focus({ preventScroll: true })
      } else if (!entry.panel.contains(document.activeElement) || document.activeElement === entry.panel) {
        e.preventDefault()
        const target = e.shiftKey ? last : first
        target.focus({ preventScroll: true })
      } else if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus({ preventScroll: true })
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus({ preventScroll: true })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      if (document.activeElement instanceof HTMLElement && entry.panel.contains(document.activeElement)) {
        lastFocusedRef.current = document.activeElement
      }
      const wasTop = isTopModal(entry)
      const index = modalStack.indexOf(entry)
      if (index !== -1) modalStack.splice(index, 1)
      if (entryRef.current === entry) entryRef.current = null
      window.removeEventListener('keydown', onKey)
      entry.root.inert = false
      entry.root.removeAttribute('aria-hidden')
      entry.panel.removeAttribute('aria-modal')
      updateModalLayers()
      if (modalStack.length === 0) document.body.style.overflow = previousBodyOverflow
      if (!wasTop) return
      const top = modalStack[modalStack.length - 1]
      if (restoreFocus?.isConnected && !restoreFocus.closest('[inert]') && (!top || top.panel.contains(restoreFocus))) {
        restoreFocus.focus({ preventScroll: true })
      } else if (top) {
        focusInitialElement(top.panel)
      }
    }
  }, [open, parentId, titleId])

  function requestClose() {
    if (isTopModal(entryRef.current) && dismissibleRef.current) onCloseRef.current()
  }

  if (!open) return null

  const isDrawer = variant === 'drawer'

  return createPortal(
    <ModalParentContext.Provider value={titleId}>
      <div
        ref={rootRef}
        className={['pf-dialog-root', isDrawer ? 'pf-dialog-root--drawer' : ''].filter(Boolean).join(' ')}
        role="presentation"
      >
        <div
          className="pf-dialog-veil"
          aria-hidden
          onMouseDown={requestClose}
        />
        <div
          ref={panelRef}
          className={[
            'pf-modal-panel',
            isDrawer ? 'pf-modal-panel--drawer' : '',
            !isDrawer && size === 'lg' ? 'pf-modal-panel--lg' : '',
            !isDrawer && size === 'xl' ? 'pf-modal-panel--xl' : '',
            className,
          ]
            .filter(Boolean)
            .join(' ')}
          role="dialog"
          tabIndex={-1}
          aria-labelledby={title ? titleId : undefined}
          aria-label={title ? undefined : t('common.dialog')}
        >
          {title ? (
            <header className="pf-modal-head">
              <h3 id={titleId}>{title}</h3>
              {dismissible ? (
                <button type="button" className="pf-modal-close" onClick={requestClose} aria-label={t('common.close')}>
                  ×
                </button>
              ) : null}
            </header>
          ) : null}
          <div className="pf-modal-body">{children}</div>
          {footer ? <footer className="pf-modal-foot">{footer}</footer> : null}
        </div>
      </div>
    </ModalParentContext.Provider>,
    document.body,
  )
}
