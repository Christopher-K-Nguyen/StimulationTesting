function [File,isQuit] = setExperimentInfo(File)
try
    %% Subject Name
    [File,isQuit] = getExperimentInfo(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end
    fprintf('\n');

    %% Experiment Type
    [File,isQuit] = getExperimentTest(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end
    % fprintf('\n');
    % expType = File.Test.Experiment;
    % isVT = contains2(expType,'VT');
    % isPS = contains2(expType,'PS');

    %% Device Type
    [File,isQuit] = getDeviceType(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end
    % fprintf('\n');

    %% Channels
    [File,isQuit] = selectChannels2(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end
    % fprintf('\n');

    %% Configuration
    [File,isQuit] = getConfiguration(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end
    
    %% Environment
    [File,isQuit] = getEnvironment(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end
    fprintf('\n');

    %% Working Electrode
    [File,isQuit] = getWorkingElectrode(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end
    % fprintf('\n');

    %% Surface Area
    [File,isQuit] = getElectrodeArea(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end

    %% Reference Electrode
    [File,isQuit] = getReferenceElectrode(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end

    %% Stimulation Parameters
    [File,isQuit] = getParameters(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end

    %% Test Parameters
    [File,isQuit] = getTest(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end

    %% Camera
    [File,isQuit] = getCamera(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end

catch err
    report = getReport(err);
    display(report);
    isQuit = true;
end

end