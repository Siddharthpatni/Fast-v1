import { Warning, X } from '@phosphor-icons/react';
import { useLang } from '../../i18n';

interface NoticeProps {
  message: string;
  actionLabel?: string;
  onAction?: () => void;
  onDismiss: () => void;
}

/** Signal-yellow disruption notice over the map, like a platform service notice. */
export default function Notice({ message, actionLabel, onAction, onDismiss }: NoticeProps) {
  const { t } = useLang();
  return (
    <div className="notice" role="status">
      <Warning className="notice__sign" size={24} weight="fill" aria-hidden="true" />
      <p className="notice__text">{message}</p>
      {actionLabel && onAction && (
        <button type="button" className="btn btn--small" onClick={onAction}>
          {actionLabel}
        </button>
      )}
      <button type="button" className="notice__close" onClick={onDismiss} aria-label={t('Dismiss notice')}>
        <X size={18} aria-hidden="true" />
      </button>
    </div>
  );
}
