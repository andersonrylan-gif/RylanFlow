import multiprocessing

if __name__ == "__main__":
    # In a frozen app, multiprocessing helpers re-run this executable; this call makes them run
    # the helper code instead of starting another copy of the app. It comes before importing the
    # app so a helper doesn't load the whole app (Whisper, audio, the menu bar) for nothing.
    multiprocessing.freeze_support()

    from rylanflow.app import main

    main()
