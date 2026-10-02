import multiprocessing

from rylanflow.app import main

if __name__ == "__main__":
    # In a frozen app, multiprocessing helpers re-run this executable; this call makes them
    # run the helper code instead of starting another copy of the app.
    multiprocessing.freeze_support()
    main()
