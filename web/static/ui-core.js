(() => {
  function byId(id) {
    return document.getElementById(id);
  }

  function isDialogElement(element) {
    return (
      typeof HTMLDialogElement !== "undefined" &&
      element instanceof HTMLDialogElement
    );
  }

  function modalIsOpen(id) {
    const modal = byId(id);
    if (!modal) return false;

    if (isDialogElement(modal)) {
      return modal.open;
    }

    return !modal.classList.contains("hidden");
  }

  function syncBodyModalState(modalIds = []) {
    const shouldLock = modalIds.some((modalId) => modalIsOpen(modalId));
    document.body.classList.toggle("modal-open", shouldLock);
    return shouldLock;
  }

  window.SpeedPulseUiCore = Object.freeze({
    byId,
    isDialogElement,
    modalIsOpen,
    syncBodyModalState,
  });
})();
