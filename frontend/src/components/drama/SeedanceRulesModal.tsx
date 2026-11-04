import { useI18n } from '../../i18n'
/** Seedance 传值与脚本规则说明弹窗（分集编辑页，对齐 docs/EPISODE_RULES.md） */
import { useState } from 'react'
import Modal from '../ui/Modal'
import {
  DRAMA_SEGMENT_DURATION_MAX,
  DRAMA_SEGMENT_DURATION_MIN,
  DRAMA_SHOT_DURATION_HARD_MAX,
  FRAGMENT_CONTENT_DURATION_MAX,
} from '../../lib/dramaEpisodePromptEditor'
import {
  DIALOGUE_PREFIX,
  DRAMA_NARRATION_PREFIX,
  DRAMA_SUBTITLE_CUE,
  VISUAL_PREFIX,
} from '../../lib/dramaEpisodeScriptValidate'

type Tab = 'payload' | 'script' | 'usage'

type Props = {
  open: boolean
  onClose: () => void
}

// 渲染 Seedance 规则说明弹窗
export function SeedanceRulesModal({ open, onClose }: Props) {
  const { t: translateUi } = useI18n()
  const [tab, setTab] = useState<Tab>('payload')

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={translateUi("workflow.seedanceInputsAndUsage")}
      size="lg"
      className="pf-help-modal seedance-rules-modal"
      footer={
        <button type="button" className="pf-btn pf-btn-lime pf-btn-sm" onClick={onClose}>
          {translateUi("workflow.gotIt")}
        </button>
      }
    >
      <div className="pf-help">
        <p className="pf-help-lede">
          {translateUi("workflow.storyboardVideosUseSeedanceSMultimodalApiSeeHow", { v1: ' ' })}<code>docs/EPISODE_RULES.md</code>）。
        </p>

        <div className="pf-help-tabs" role="tablist" aria-label={translateUi("workflow.seedanceGuideSections")}>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'payload'}
            className={tab === 'payload' ? 'active' : undefined}
            onClick={() => setTab('payload')}
          >
            {translateUi("workflow.inputMapping")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'script'}
            className={tab === 'script' ? 'active' : undefined}
            onClick={() => setTab('script')}
          >
            {translateUi("workflow.scriptFormat")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'usage'}
            className={tab === 'usage' ? 'active' : undefined}
            onClick={() => setTab('usage')}
          >
            {translateUi("workflow.usageTips")}
          </button>
        </div>

        {tab === 'payload' ? (
          <div className="seedance-rules-section">
            <h4>{translateUi("workflow.toolbarSettingsApiFields")}</h4>
            <ul className="seedance-rules-list">
              <li>
                <strong>{translateUi("workflow.model")}</strong> → <code>model</code>（Seedance 2.5 / 1.5）
              </li>
              <li>
                <strong>{translateUi("workflow.aspectRatioResolution")}</strong>{translateUi("workflow.episodeToolbarSettings")} <code>ratio</code>、<code>resolution</code>
              </li>
              <li>
                <strong>{translateUi("workflow.duration")}</strong> → <code>duration</code>{translateUi("workflow.inTheScript", { v1: ' ' })}<code>@duration</code> {translateUi("workflow.totalRecommended4SecondsWithoutTagsUsesThisShot", { v1: FRAGMENT_CONTENT_DURATION_MAX })}</li>
              <li>
                <strong>{translateUi("workflow.videoStyle")}</strong> {translateUi("workflow.addedToTheRequiredVisualStylePromptBlock")}
              </li>
            </ul>

            <h4>{translateUi("workflow.multimodalContentArraySubmittedInOrder")}</h4>
            <ol className="seedance-rules-list">
              <li>
                <strong>text</strong>{translateUi("workflow.theAssembledPromptRequiredBlocksBelowFollowedByThe")}
              </li>
              <li>
                <strong>reference_image</strong>{translateUi("workflow.coverOrMainImagesOfCharactersScenesAndProps")}
              </li>
              <li>
                <strong>reference_audio</strong>{translateUi("workflow.voiceSamplesForSpeakingCharactersAndNarrationWithBound")}
              </li>
            </ol>
            <p className="seedance-rules-note">
              {translateUi("workflow.enabledByDefault")} <code>generate_audio</code>{translateUi("workflow.seedanceGeneratesSpeechWhereRequestedSubtitlesAreAddedLater")}
            </p>

            <h4>{translateUi("workflow.promptAssemblyOrder")}</h4>
            <ol className="seedance-rules-list">
              <li>{translateUi("workflow.selectedProjectVisualStyle")}</li>
              <li>{translateUi("workflow.inferredFromNarrationDialogueAndVisualCues")}</li>
              <li>{translateUi("workflow.characterNameAudioReferenceNumberAssignedVoiceSpeakingIn")}</li>
              <li>{translateUi("workflow.narratorAudioReferenceNumberAssignedProjectNarratorVoice")}</li>
              <li>{translateUi("workflow.characterNameImageReferenceNumber")}</li>
              <li>{translateUi("workflow.sceneNameImageReferenceNumber")}</li>
              <li>{translateUi("workflow.propNameImageReferenceNumber")}</li>
              <li>
                {translateUi("workflow.scriptReplace")} <code>@asset:ID</code> {translateUi("workflow.replacedByNameReferenceImageN", { v1: ' ' })}<code>@duration:N</code> {translateUi("workflow.withTimeRangesSuchAs00000004")}
              </li>
            </ol>
          </div>
        ) : null}

        {tab === 'script' ? (
          <div className="seedance-rules-section">
            <h4>{translateUi("workflow.durationTags")}</h4>
            <ul className="seedance-rules-list">
              <li>
                <code>@duration:N</code>{translateUi("workflow.markASegmentSDurationInSecondsRecommendedPer", { v1: ' ', v2: DRAMA_SEGMENT_DURATION_MIN, v3: DRAMA_SEGMENT_DURATION_MAX })}</li>
              <li>
                {translateUi("workflow.recommendedTotalPerNewShotSecondsOlderScriptsUp", { v1: FRAGMENT_CONTENT_DURATION_MAX, v2: DRAMA_SHOT_DURATION_HARD_MAX })}</li>
              <li>{translateUi("workflow.type")} <code>@</code> {translateUi("workflow.toInsertADurationChipOrAssetReference")}</li>
            </ul>

            <h4>{translateUi("workflow.assetReferences")}</h4>
            <ul className="seedance-rules-list">
              <li>
                <code>@asset:123</code>{translateUi("workflow.referenceCharacterSceneOrPropId123InThe")}
              </li>
              <li>{translateUi("workflow.clickAnAssetOnTheLeftOrInThis")}</li>
              <li>{translateUi("workflow.referencedCharactersNeedReferenceImagesBindVoiceSamplesFor")}</li>
            </ul>

            <h4>{translateUi("workflow.commonDramaCuesAvoidTheExplainerStyleBurnAll")}</h4>
            <div className="seedance-rules-examples">
              <code>{DRAMA_SUBTITLE_CUE}</code>
              <code>【BGM：低沉史诗，音量低于人声】</code>
              <code>@duration:4</code>
              <code>{VISUAL_PREFIX}空镜：浑浊黄河拍击老石……</code>
              <code>@duration:6</code>
              <code>{DIALOGUE_PREFIX}禹：水患未平，岂能退！</code>
              <code>{DRAMA_NARRATION_PREFIX}千年后，人们仍记得这一战。</code>
            </div>
            <ul className="seedance-rules-list">
              <li>
                <strong>{translateUi("workflow.establishingShotsShotSizes")}</strong>{translateUi("workflow.useEstablishingWideOrCloseUpShotCuesOr", { v1: ' ' })}<code>{VISUAL_PREFIX}</code>；<strong>{translateUi("workflow.doNot")}</strong>{translateUi("workflow.markAsDialogueOrNarrationItWouldBeSpoken")}
              </li>
              <li>
                <strong>{translateUi("workflow.dialogue")}</strong>：<code>角色名：台词</code> {translateUi("workflow.or", { v1: DIALOGUE_PREFIX })}</li>
              <li>
                <strong>{translateUi("compliance.fields.narration")}</strong>{translateUi("workflow.rotateSubtitlesSentenceBySentenceInSyncWithSpeech", { v1: DRAMA_NARRATION_PREFIX })}</li>
              <li>
                <strong>{translateUi("workflow.characterIntroductions")}</strong>{translateUi("workflow.placeTheIntroductionForEachImportantCharacterSFirst")}<strong>{translateUi("workflow.besideThatCharacter")}</strong>{translateUi("workflow.notAsABottomSubtitleOrCenteredTitle")}
              </li>
              <li>
                <strong>BGM</strong>{translateUi("workflow.keepVolumeBelowSpeech")}
              </li>
            </ul>

            <h4>{translateUi("workflow.shotSizeCameraMovementTools")}</h4>
            <ul className="seedance-rules-list">
              <li>
                {translateUi("workflow.inTheEditorType")} <code>@</code> {translateUi("workflow.toolsShotSizeCameraToInsertEstablishingCloseUp")}
              </li>
              <li>{translateUi("workflow.formulaSubjectActionSettingShotCameraLightingKeepMovement")}</li>
              <li>{translateUi("workflow.largeRotationsCanDistortFacesInCloseUpsUse")}</li>
            </ul>
          </div>
        ) : null}

        {tab === 'usage' ? (
          <div className="seedance-rules-section">
            <h4>{translateUi("workflow.beforeGenerating")}</h4>
            <ul className="seedance-rules-list">
              <li>{translateUi("workflow.checkScriptDurationAndEnsureEstablishingShotsAreNot")}</li>
              <li>{translateUi("workflow.referencedCharactersNeedImagesVoiceSamplesAreRecommendedFor")}</li>
              <li>{translateUi("workflow.confirmAspectRatioAndResolutionInProjectSettingsThen")}</li>
            </ul>

            <h4>{translateUi("workflow.queueAndConcurrency")}</h4>
            <ul className="seedance-rules-list">
              <li>{translateUi("workflow.storyboardClipsUseASeparateVideoQueueWithUp")}</li>
              <li>{translateUi("workflow.whileAShotGeneratesYouCanEditOtherShots")}</li>
              <li>{translateUi("workflow.batchGenerationQueuesThisEpisodeSShotsAShot")}</li>
            </ul>

            <h4>{translateUi("workflow.lastFrameContinuityBetweenShots")}</h4>
            <ul className="seedance-rules-list">
              <li>
                {translateUi("workflow.generationDefaultsTo")} <code>return_last_frame=true</code>{translateUi("workflow.afterSuccessSavesTheLastFrameToTheStoryboard", { v1: ' ' })}<code>params.lastFrameUrl</code>
              </li>
              <li>
                {translateUi("workflow.whenEpisodeContinuityIsEnabledAndAShotHas")} <code>reference_image</code> {translateUi("workflow.atTheEndSeedanceDoesNotAllowMixingIt")} <code>first_frame</code> {translateUi("workflow.onlyShotsWithoutReferenceMediaUse")} <code>first_frame</code>
              </li>
              <li>
                {translateUi("workflow.avoidEditOrExtendInstructionsInOrdinaryScriptsWhich")}
              </li>
            </ul>

            <h4>{translateUi("workflow.voicesAndReferenceAudio")}</h4>
            <ul className="seedance-rules-list">
              <li>{translateUi("workflow.bindACharacterSVoiceSampleInAssetsThe")}</li>
              <li>{translateUi("workflow.withoutABoundVoiceSeedanceGenerateAudioChoosesThe")}</li>
              <li>{translateUi("workflow.generateOrUploadReferenceImagesForLinkedCharactersScenes")}</li>
            </ul>

            <h4>{translateUi("workflow.replanStoryboardsWithAi")}</h4>
            <p className="seedance-rules-note">
              {translateUi("workflow.replansShotScriptsAndDurationStructureFromTheScript")}
            </p>

          </div>
        ) : null}
      </div>
    </Modal>
  )
}
