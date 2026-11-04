import { useI18n } from '../../../../i18n'
/** 选中节点顶部：本地上传 + 从全局资产库选择 + 角色音色生成/绑定/试听 */
import { useCallback, useRef, useState, type ChangeEvent, type MouseEvent } from 'react'
import { AudioLines, FolderOpen, Loader2, Upload } from 'lucide-react'
import { dramaApi, type DramaAsset } from '../../../../api/drama'
import { CharacterVoicePreviewButton } from '../../../../components/drama/CharacterVoicePreviewButton'
import { generateAndBindCharacterVoice } from '../../../../lib/characterVoiceGenerate'
import { DRAMA_VOICE_BINDING_ENABLED } from '../../../../lib/dramaVoiceBinding'
import { CharacterVoiceBindModal } from '../../CharacterVoiceBindModal'
import { useCanvasStore } from '../CanvasStore'
import { CANVAS_UPLOADABLE_KINDS, type CanvasNodeKind } from '../canvasTypes'
import {
  canvasKindToLibraryTypes,
  GlobalAssetPickerModal,
} from '../../GlobalAssetPickerModal'

type CanvasNodeUploadBarProps = {
  nodeId: string
  kind: CanvasNodeKind
  /** 角色节点已绑定音色名 */
  voiceLabel?: string | null
  /** 角色节点已绑定音色试听地址 */
  voiceUrl?: string | null
}

const IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp,image/gif'
const AUDIO_ACCEPT =
  'audio/mpeg,audio/mp3,audio/wav,audio/x-wav,audio/mp4,audio/m4a,audio/aac,audio/ogg,audio/webm,.mp3,.wav,.m4a,.aac,.ogg,.webm'

/** 渲染选中节点的上传与资产库操作条 */
export function CanvasNodeUploadBar({
  nodeId,
  kind,
  voiceLabel,
  voiceUrl,
}: CanvasNodeUploadBarProps) {
  const { t: translateUi } = useI18n()
  /*
   * uploading 本地上传中
   * pickerOpen 资产库弹窗
   * voiceLoading 一键生成音色中
   * bindOpen 绑定/更换弹窗
   * bindAsset 弹窗用角色资产快照
   * bindBusy 打开弹窗前拉资产中
   */
  const {
    uploadNodeMedia,
    applyLibraryMediaToNode,
    syncNodeFromAsset,
    ensureNodeAsset,
    setErrorMessage,
    projectId,
  } = useCanvasStore()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [voiceLoading, setVoiceLoading] = useState(false)
  const [bindOpen, setBindOpen] = useState(false)
  const [bindAsset, setBindAsset] = useState<DramaAsset | null>(null)
  const [bindBusy, setBindBusy] = useState(false)

  const stopFlowEvent = useCallback((event: MouseEvent) => {
    event.stopPropagation()
  }, [])

  if (!CANVAS_UPLOADABLE_KINDS.has(kind)) return null

  const isCharacter = kind === 'character'
  const isAudio = kind === 'audio'
  const hasVoice = Boolean(voiceUrl || voiceLabel)
  const uploadLabel = isAudio ? translateUi("workflow.uploadAudio") : translateUi("workflow.uploadImage")
  const accept = isAudio ? AUDIO_ACCEPT : IMAGE_ACCEPT
  const toolbarBusy = uploading || voiceLoading || bindBusy

  /** 确保角色资产存在并打开绑定弹窗 */
  async function openVoiceBindModal() {
    if (bindBusy || voiceLoading) return
    setBindBusy(true)
    try {
      const assetId = await ensureNodeAsset(nodeId)
      const list = await dramaApi.listAssets(projectId)
      const asset = list.find((a) => a.id === assetId)
      if (!asset) throw new Error(translateUi("workflow.characterAssetNotFound"))
      setBindAsset(asset)
      setBindOpen(true)
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : translateUi("workflow.couldNotOpenVoiceAssignment"))
    } finally {
      setBindBusy(false)
    }
  }

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return

    if (isAudio) {
      const name = (file.name || '').toLowerCase()
      const okType =
        file.type.startsWith('audio/') || /\.(mp3|wav|m4a|aac|ogg|webm)$/i.test(name)
      if (!okType) {
        setErrorMessage(translateUi("workflow.chooseAnAudioFileMp3WavM4aEtc"))
        return
      }
      if (file.size > 30 * 1024 * 1024) {
        setErrorMessage(translateUi("workflow.audioMustBeNoLargerThan30Mb"))
        return
      }
    } else {
      if (!file.type.startsWith('image/')) {
        setErrorMessage(translateUi("workflow.chooseAnImageFile"))
        return
      }
      if (file.size > 20 * 1024 * 1024) {
        setErrorMessage(translateUi("workflow.imagesMustBeNoLargerThan20Mb"))
        return
      }
    }

    setUploading(true)
    void uploadNodeMedia(nodeId, file)
      .catch((err) => setErrorMessage(err instanceof Error ? err.message : translateUi("workflow.uploadFailed")))
      .finally(() => setUploading(false))
  }

  /** 一键 AI 生成音色并绑定到角色节点 */
  async function handleGenerateVoice() {
    if (voiceLoading) return
    setVoiceLoading(true)
    try {
      const assetId = await ensureNodeAsset(nodeId)
      const list = await dramaApi.listAssets(projectId)
      const asset = list.find((a) => a.id === assetId)
      if (!asset) throw new Error(translateUi("workflow.characterAssetNotFound"))
      const { character } = await generateAndBindCharacterVoice(projectId, asset)
      // syncNodeFromAsset 内部 markDirty，写入 voiceLabel/voiceUrl 并触发自动保存
      syncNodeFromAsset(nodeId, character)
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : translateUi("workflow.voiceGenerationFailed"))
    } finally {
      setVoiceLoading(false)
    }
  }

  return (
    <>
      <div
        className="fc-node-toolbar nodrag nopan"
        onMouseDown={stopFlowEvent}
        onPointerDown={stopFlowEvent}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept={accept}
          className="fc-hidden-input"
          onChange={handleFileChange}
        />
        <button
          type="button"
          className="fc-toolbar-chip"
          disabled={toolbarBusy}
          onClick={() => fileInputRef.current?.click()}
        >
          {uploading ? <Loader2 size={14} className="fc-spin" /> : <Upload size={14} strokeWidth={1.8} />}
          {uploading ? translateUi("workflow.uploading") : uploadLabel}
        </button>
        <button
          type="button"
          className="fc-toolbar-chip"
          disabled={toolbarBusy}
          onClick={() => setPickerOpen(true)}
        >
          <FolderOpen size={14} strokeWidth={1.8} />
          {translateUi("workflow.chooseFromAssetLibrary")}
        </button>
        {DRAMA_VOICE_BINDING_ENABLED && isCharacter ? (
          hasVoice && voiceUrl ? (
            <>
              <CharacterVoicePreviewButton
                url={voiceUrl}
                label={voiceLabel || undefined}
                variant="chip"
                className="is-active"
                onError={setErrorMessage}
              />
              <button
                type="button"
                className="fc-toolbar-chip"
                disabled={toolbarBusy}
                onClick={() => void openVoiceBindModal()}
                title={translateUi("workflow.changeOrUnbindTheVoiceOrConnectADifferent")}
              >
                {bindBusy ? <Loader2 size={14} className="fc-spin" /> : <AudioLines size={14} strokeWidth={1.8} />}
                {bindBusy ? translateUi("workflow.opening_a83d45") : translateUi("workflow.replace")}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="fc-toolbar-chip"
                disabled={toolbarBusy}
                onClick={() => void handleGenerateVoice()}
                title={translateUi("workflow.generateAnAiVoiceFromTheCharacterDescription")}
              >
                {voiceLoading ? (
                  <Loader2 size={14} className="fc-spin" />
                ) : (
                  <AudioLines size={14} strokeWidth={1.8} />
                )}
                {voiceLoading ? translateUi("workflow.generating") : translateUi("workflow.generateVoice")}
              </button>
              <button
                type="button"
                className="fc-toolbar-chip"
                disabled={toolbarBusy}
                onClick={() => void openVoiceBindModal()}
                title={translateUi("workflow.uploadOrChooseAVoiceOrConnectAnAudio")}
              >
                {bindBusy ? <Loader2 size={14} className="fc-spin" /> : null}
                {bindBusy ? translateUi("workflow.opening_a83d45") : translateUi("workflow.bindVoice")}
              </button>
            </>
          )
        ) : null}
      </div>

      <GlobalAssetPickerModal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        projectId={projectId}
        defaultTab="all"
        allowedTypes={canvasKindToLibraryTypes(kind)}
        title={translateUi("workflow.chooseFromAssetLibrary")}
        confirmLabel={translateUi("workflow.useSelected")}
        onPick={async (source) => {
          await applyLibraryMediaToNode(nodeId, source)
        }}
      />

      {DRAMA_VOICE_BINDING_ENABLED && bindAsset ? (
        <CharacterVoiceBindModal
          asset={bindAsset}
          projectId={projectId}
          open={bindOpen}
          onClose={() => {
            setBindOpen(false)
            setBindAsset(null)
          }}
          onBound={(updated) => {
            syncNodeFromAsset(nodeId, updated)
            setBindOpen(false)
            setBindAsset(null)
          }}
          onError={setErrorMessage}
        />
      ) : null}
    </>
  )
}
