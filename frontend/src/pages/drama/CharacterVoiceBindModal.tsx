import { useI18n } from '../../i18n'
import { translate as translateUi } from '../../i18n/runtime'
/** 角色音色绑定：选择 / 上传 / AI 合成试听，写入 params.voiceAudio */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { dramaApi, resolveDramaMediaUrl, type DramaAsset } from '../../api/drama'
import Modal from '../../components/ui/Modal'

const AUDIO_ACCEPT = 'audio/mpeg,audio/mp3,audio/wav,audio/x-wav,audio/mp4,audio/m4a,.mp3,.wav,.m4a'

/** 是否可作为角色音色资产 */
function isVoiceLikeAsset(asset: DramaAsset): boolean {
  const t = (asset.type || '').toLowerCase()
  return t === 'voice' || t === 'audio'
}

export type VoiceBinding = {
  sourceAssetId: number
  url: string
  label: string
  voicePrompt?: string
}

type Props = {
  asset: DramaAsset
  projectId: number
  open: boolean
  onClose: () => void
  onBound: (asset: DramaAsset) => void
  onError: (message: string) => void
}

// 读取 voice 资产已保存的 speaker
function readVoiceSpeaker(asset: DramaAsset): string {
  const params = (asset.params || {}) as Record<string, unknown>
  return typeof params.speaker === 'string' ? params.speaker.trim() : ''
}

// 读取 voice 资产的音色描述
export function readVoicePrompt(asset: DramaAsset): string {
  const params = (asset.params || {}) as Record<string, unknown>
  return typeof params.voicePrompt === 'string' ? params.voicePrompt.trim() : ''
}

// 从角色资产 params 读取已绑定音色
export function readAssetVoiceBinding(asset: DramaAsset): VoiceBinding | null {
  const params = (asset.params || {}) as Record<string, unknown>
  const raw = params.voiceAudio
  if (raw && typeof raw === 'object') {
    const data = raw as Record<string, unknown>
    const sourceAssetId =
      typeof data.sourceAssetId === 'number'
        ? data.sourceAssetId
        : typeof data.voiceId === 'string'
          ? Number.NaN
          : null
    const url =
      typeof data.url === 'string'
        ? data.url
        : typeof data.previewUrl === 'string'
          ? data.previewUrl
          : ''
    if (typeof sourceAssetId === 'number' && sourceAssetId > 0 && url) {
      return {
        sourceAssetId,
        url,
        label: typeof data.label === 'string' ? data.label : translateUi("daihuo.video.voice"),
        voicePrompt: typeof data.voicePrompt === 'string' ? data.voicePrompt : undefined,
      }
    }
  }
  const canvas = params.canvas
  if (canvas && typeof canvas === 'object') {
    const voiceAudio = (canvas as Record<string, unknown>).voiceAudio
    if (voiceAudio && typeof voiceAudio === 'object') {
      const data = voiceAudio as Record<string, unknown>
      const sourceAssetId = typeof data.sourceAssetId === 'number' ? data.sourceAssetId : null
      const url = typeof data.url === 'string' ? data.url : ''
      if (sourceAssetId && url) {
        return { sourceAssetId, url, label: translateUi("daihuo.video.voice") }
      }
    }
  }
  return null
}

// 构建绑定后的 params（同时写 voiceAudio 与 canvas.voiceAudio）
export function buildBoundParams(asset: DramaAsset, voice: DramaAsset): Record<string, unknown> {
  const url = voice.url || ''
  const binding: VoiceBinding = {
    sourceAssetId: voice.id,
    url,
    label: voice.name || translateUi("daihuo.video.voice"),
    voicePrompt: readVoicePrompt(voice) || undefined,
  }
  const prev = (asset.params || {}) as Record<string, unknown>
  const prevCanvas =
    prev.canvas && typeof prev.canvas === 'object'
      ? (prev.canvas as Record<string, unknown>)
      : {}
  return {
    ...prev,
    voiceAudio: binding,
    canvas: {
      ...prevCanvas,
      voiceAudio: { sourceAssetId: voice.id, url },
    },
  }
}

// 渲染角色音色绑定弹窗
export function CharacterVoiceBindModal({
  asset,
  projectId,
  open,
  onClose,
  onBound,
  onError,
}: Props) {
  const { t: translateUi } = useI18n()
  /*
   * voiceAssets 项目内 voice/audio 资产
   * selectedId 选中音色
   * newPrompt / newName 新建合成表单
   * busy / synthBusy / promptBusy / uploadBusy 各操作中
   * mode pick | upload | create
   */
  const [voiceAssets, setVoiceAssets] = useState<DramaAsset[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [newPrompt, setNewPrompt] = useState('')
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [synthBusy, setSynthBusy] = useState(false)
  const [promptBusy, setPromptBusy] = useState(false)
  const [uploadBusy, setUploadBusy] = useState(false)
  const [suggestedSpeaker, setSuggestedSpeaker] = useState('')
  const [mode, setMode] = useState<'pick' | 'upload' | 'create'>('pick')
  const promptRequestedRef = useRef(false)
  const uploadInputRef = useRef<HTMLInputElement>(null)

  const bound = useMemo(() => readAssetVoiceBinding(asset), [asset])
  const selectedVoice = voiceAssets.find((v) => v.id === selectedId) || null
  const previewUrl = selectedVoice?.url ? resolveDramaMediaUrl(selectedVoice.url) : ''

  // 根据角色设定 AI 生成音色描述
  const fetchVoicePrompt = useCallback(
    async (force = false) => {
      if (promptBusy) return
      if (!force && newPrompt.trim()) return
      setPromptBusy(true)
      try {
        const result = await dramaApi.suggestVoicePrompt({
          project_id: projectId,
          asset_id: asset.id,
        })
        setNewPrompt(result.voice_prompt || '')
        setSuggestedSpeaker(result.speaker || '')
      } catch (err) {
        onError(err instanceof Error ? err.message : translateUi("workflow.couldNotGenerateTheVoiceDescription"))
      } finally {
        setPromptBusy(false)
      }
    },
    [asset.id, newPrompt, onError, projectId, promptBusy],
  )

  useEffect(() => {
    if (!open) {
      promptRequestedRef.current = false
      return
    }
    setSelectedId(bound?.sourceAssetId ?? null)
    setNewPrompt('')
    setSuggestedSpeaker('')
    setNewName(`${asset.name || '角色'}音色`)
    setMode('pick')
    promptRequestedRef.current = false

    dramaApi
      .listAssets(projectId)
      .then((list) => {
        const voices = list.filter(isVoiceLikeAsset)
        setVoiceAssets(voices)
        if (!bound?.sourceAssetId && voices[0]) {
          setSelectedId(voices[0].id)
        }
        if (voices.length === 0) {
          setMode('upload')
        }
      })
      .catch((err) => onError(err instanceof Error ? err.message : translateUi("workflow.couldNotLoadVoices")))
  }, [open, asset, projectId, bound?.sourceAssetId, onError])

  // 进入「AI 合成」时自动生成音色描述
  useEffect(() => {
    if (!open || mode !== 'create' || promptRequestedRef.current) return
    promptRequestedRef.current = true
    void fetchVoicePrompt(true)
  }, [open, mode, fetchVoicePrompt])

  if (!open) return null

  const anyBusy = busy || synthBusy || promptBusy || uploadBusy

  // 本地上传音频并记为音色资产
  async function handleUploadAudio(file: File) {
    if (uploadBusy) return
    if (!file.type.startsWith('audio/') && !/\.(mp3|wav|m4a)$/i.test(file.name)) {
      onError(translateUi("workflow.uploadAnMp3WavOrM4aAudioFile"))
      return
    }
    setUploadBusy(true)
    try {
      const baseName = file.name.replace(/\.[^.]+$/, '').trim()
      const created = await dramaApi.createAsset({
        project_id: projectId,
        type: 'voice',
        asset_type: 'audio',
        name: baseName || `${asset.name || '角色'}音色`,
        params: {
          source: 'upload',
          originalFilename: file.name,
        },
      })
      const uploaded = await dramaApi.uploadAssetMedia(created.id, file)
      if (!(uploaded.url || '').trim()) throw new Error(translateUi("workflow.theUploadSucceededButReturnedNoPlayableUrl"))
      setVoiceAssets((prev) => [uploaded, ...prev.filter((v) => v.id !== uploaded.id)])
      setSelectedId(uploaded.id)
      setMode('pick')
    } catch (err) {
      onError(err instanceof Error ? err.message : translateUi("workflow.uploadFailed"))
    } finally {
      setUploadBusy(false)
      if (uploadInputRef.current) uploadInputRef.current.value = ''
    }
  }

  // 按提示词新建并合成 voice 资产
  async function handleCreateAndSynth() {
    const prompt = newPrompt.trim()
    if (!prompt || synthBusy) return
    setSynthBusy(true)
    try {
      const result = await dramaApi.generateVoice({
        project_id: projectId,
        name: newName.trim() || undefined,
        voice_prompt: prompt,
        speaker: suggestedSpeaker || undefined,
        character_asset_id: asset.id,
      })
      const created = result.asset
      if (!created) throw new Error(translateUi("workflow.compositionFailed"))
      setVoiceAssets((prev) => [...prev, created])
      setSelectedId(created.id)
      setMode('pick')
    } catch (err) {
      onError(err instanceof Error ? err.message : translateUi("workflow.voiceSynthesisFailed"))
    } finally {
      setSynthBusy(false)
    }
  }

  // 对已有 voice 资产重新合成
  async function handleResynth(voice: DramaAsset) {
    const prompt = readVoicePrompt(voice)
    if (!prompt || synthBusy) return
    setSynthBusy(true)
    try {
      const result = await dramaApi.generateVoice({
        project_id: projectId,
        asset_id: voice.id,
        voice_prompt: prompt,
        speaker: readVoiceSpeaker(voice) || suggestedSpeaker || undefined,
        character_asset_id: asset.id,
      })
      if (result.asset) {
        setVoiceAssets((prev) => prev.map((v) => (v.id === voice.id ? result.asset! : v)))
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : translateUi("workflow.couldNotSynthesizeAgain"))
    } finally {
      setSynthBusy(false)
    }
  }

  // 确认绑定到角色
  async function handleConfirm() {
    if (!selectedVoice?.url || busy) {
      onError(translateUi("workflow.selectOrUploadPlayableAudioFirst"))
      return
    }
    setBusy(true)
    try {
      const updated = await dramaApi.updateAsset(asset.id, {
        params: buildBoundParams(asset, selectedVoice),
      })
      onBound(updated)
      onClose()
    } catch (err) {
      onError(err instanceof Error ? err.message : translateUi("workflow.couldNotAssignTheVoice"))
    } finally {
      setBusy(false)
    }
  }

  // 解除绑定
  async function handleUnbind() {
    if (busy) return
    setBusy(true)
    try {
      const prev = (asset.params || {}) as Record<string, unknown>
      const nextParams = { ...prev }
      delete nextParams.voiceAudio
      if (nextParams.canvas && typeof nextParams.canvas === 'object') {
        const canvas = { ...(nextParams.canvas as Record<string, unknown>) }
        delete canvas.voiceAudio
        nextParams.canvas = canvas
      }
      const updated = await dramaApi.updateAsset(asset.id, { params: nextParams })
      onBound(updated)
      onClose()
    } catch (err) {
      onError(err instanceof Error ? err.message : translateUi("workflow.couldNotUnassignTheVoice"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={translateUi("workflow.bindVoice")}
      size="lg"
      dismissible={!anyBusy}
      className="drama-voice-bind-modal"
      footer={
        <>
          <button type="button" className="pf-btn" onClick={onClose} disabled={anyBusy}>
            {translateUi("ecomSet.copyModal.cancel")}
          </button>
          {bound ? (
            <button
              type="button"
              className="pf-btn"
              onClick={() => void handleUnbind()}
              disabled={anyBusy}
            >
              {translateUi("workflow.unbind")}
            </button>
          ) : null}
          <button
            type="button"
            className="pf-btn pf-btn-lime"
            onClick={() => void handleConfirm()}
            disabled={!selectedVoice?.url || anyBusy}
          >
            {busy ? translateUi("workflow.binding") : translateUi("workflow.confirmBinding")}
          </button>
        </>
      }
    >
      <p className="drama-muted">
        {translateUi("workflow.giveAVoiceChooseASampleUploadARecording", { v1: asset.name || translateUi("workflow.character") })}</p>
      <p className="drama-muted" style={{ marginTop: -6, fontSize: 12 }}>
        {translateUi("workflow.onTheCanvasConnectingAnAudioNodeToA")}
      </p>

      <div className="drama-voice-mode-tabs">
        <button
          type="button"
          className={mode === 'pick' ? 'active' : ''}
          onClick={() => setMode('pick')}
          disabled={anyBusy}
        >
          {translateUi("workflow.chooseExisting")}
        </button>
        <button
          type="button"
          className={mode === 'upload' ? 'active' : ''}
          onClick={() => setMode('upload')}
          disabled={anyBusy}
        >
          {translateUi("workflow.uploadAudio")}
        </button>
        <button
          type="button"
          className={mode === 'create' ? 'active' : ''}
          onClick={() => {
            setMode('create')
            if (!newPrompt.trim() && !promptBusy) {
              promptRequestedRef.current = false
            }
          }}
          disabled={anyBusy}
        >
          {translateUi("workflow.synthesizeWithAi")}
        </button>
      </div>

      {mode === 'pick' ? (
        <div className="drama-voice-list">
          {voiceAssets.length === 0 ? (
            <p className="drama-muted">{translateUi("workflow.noVoicesAvailableUploadAudioOrSynthesizeAVoice")}</p>
          ) : (
            voiceAssets.map((voice) => {
              const hasAudio = Boolean(voice.url)
              const canResynth = Boolean(readVoicePrompt(voice))
              const statusLabel = !hasAudio
                ? translateUi("workflow.pendingGeneration")
                : canResynth
                  ? translateUi("workflow.synthesized")
                  : translateUi("workflow.uploaded")
              return (
                <label key={voice.id} className="drama-voice-option">
                  <input
                    type="radio"
                    name="drama-voice-asset"
                    checked={selectedId === voice.id}
                    onChange={() => setSelectedId(voice.id)}
                  />
                  <span>
                    {voice.name || translateUi("workflow.voice", { v1: String(voice.id) })}
                    <small>{statusLabel}</small>
                  </span>
                  {hasAudio && canResynth ? (
                    <button
                      type="button"
                      className="pf-btn pf-btn-sm"
                      disabled={synthBusy}
                      onClick={(e) => {
                        e.preventDefault()
                        void handleResynth(voice)
                      }}
                    >
                      {translateUi("workflow.resynthesize")}
                    </button>
                  ) : null}
                </label>
              )
            })
          )}
        </div>
      ) : mode === 'upload' ? (
        <div className="drama-voice-create-form">
          <p className="drama-muted" style={{ margin: 0 }}>
            {translateUi("workflow.supportsMp3WavAndM4aClearSpeechOfAt")}
          </p>
          <input
            ref={uploadInputRef}
            type="file"
            accept={AUDIO_ACCEPT}
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) void handleUploadAudio(file)
            }}
          />
          <button
            type="button"
            className="pf-btn pf-btn-lime"
            disabled={uploadBusy}
            onClick={() => uploadInputRef.current?.click()}
          >
            {uploadBusy ? translateUi("workflow.uploading") : translateUi("workflow.chooseAudioFile")}
          </button>
        </div>
      ) : (
        <div className="drama-voice-create-form">
          <label className="drama-field">
            <span>{translateUi("workflow.voiceName")}</span>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={translateUi("workflow.forExampleCalmManPlayfulYoungWoman")}
            />
          </label>
          <label className="drama-field">
            <span className="drama-voice-prompt-label">
              {translateUi("workflow.describeTheVoiceInYourOwnWords")}
              <button
                type="button"
                className="pf-btn pf-btn-sm"
                disabled={promptBusy || synthBusy}
                onClick={() => void fetchVoicePrompt(true)}
              >
                {promptBusy ? translateUi("workflow.generating") : translateUi("workflow.writeFromTheCharacterDescription")}
              </button>
            </span>
            <textarea
              rows={4}
              value={
                promptBusy && !newPrompt
                  ? translateUi("workflow.writingAVoiceDescriptionFromTheCharacterProfile")
                  : newPrompt
              }
              readOnly={promptBusy && !newPrompt}
              onChange={(e) => setNewPrompt(e.target.value)}
              placeholder={translateUi("workflow.forExampleAManInHisThirtiesWithA")}
            />
          </label>
          {suggestedSpeaker ? (
            <p className="drama-muted" style={{ margin: 0, fontSize: 12 }}>
              {translateUi("workflow.matchedVoice", { v1: suggestedSpeaker })}</p>
          ) : null}
          <button
            type="button"
            className="pf-btn pf-btn-lime"
            disabled={!newPrompt.trim() || synthBusy || promptBusy}
            onClick={() => void handleCreateAndSynth()}
          >
            {synthBusy ? translateUi("workflow.synthesizing") : translateUi("workflow.generateVoicePreview")}
          </button>
        </div>
      )}

      {previewUrl ? (
        <audio className="drama-voice-audio" controls src={previewUrl} />
      ) : null}
    </Modal>
  )
}
