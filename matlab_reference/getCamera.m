function [File,isQuit] = getCamera(File)
%% getCamera  Ask user to enable & configure a camera, preview selection, set sampling rate & mode
%   [File,isQuit] = getCamera(File) will:
%     • Ask whether to enable the camera
%     • Let you pick from connected cameras (with live preview)
%     • Prompt for a sampling rate (Hz)
%     • Prompt for recording mode: Continuous or Periodic
%     • If Periodic: ask for interval (s)
%     • Confirm each choice (or restart/quit)
%   On success, File.Camera, File.CameraSamplingRate, File.CameraMode,
%   and (if periodic) File.CameraInterval are set.

%% Constants
BUTTON_CONFIRM = 'Confirm';
BUTTON_TRY = 'Start Over';
BUTTON_CANCEL = 'Cancel';
opts.Default = BUTTON_CONFIRM;
opts.Interpreter = 'tex';

%% Init
isQuit = false;
File.Camera.Enable = false;

%% Step 1: Enable camera?
answer = questdlg(...
    'Enable camera?','Camera Setup',...
    'Yes','No',BUTTON_CANCEL,opts);
switch answer
    case 'Yes'
        File.Camera.Enable = true;
    case 'No'
        fprintf('Camera disabled by user.\n');
        return
    otherwise
        fprintf('Camera setup cancelled.\n');
        isQuit = true;
        return
end

%% Step 2: Select & preview camera
confirmCam = false;
while ~confirmCam
    camList = webcamlist();
    if isempty(camList)
        errordlg('No cameras detected.','Camera Error');
        isQuit = true;
        return;
    end
    [list_idx,list_tf] = listdlg(...
        'PromptString','Select a camera:',...
        'ListString',camList,...
        'SelectionMode','single',...
        'ListSize',[200 300]);
    if ~list_tf
        fprintf('Camera selection cancelled.\n');
        isQuit = true; return
    end
    camName = camList{list_idx};
    cam = webcam(camName);
    % hFig = figure( ...
    %     'Name','Camera Preview', ...
    %     'NumberTitle','off');
    himg = preview(cam);
    % msgBox = msgbox( ...
    %     'Previewing. Close this box to continue.', ...
    %     'Preview', ...
    %     'modal');
    % uiwait(msgBox);
    waitfor(himg);
    closePreview(cam);
    % close(hFig);

    quest = sprintf('Selected camera: "%s"',camName);
    choice = questdlg(quest,...
        'Confirm Camera', ...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL, ...
        opts);
    switch choice
        case BUTTON_CONFIRM
            confirmCam = true;
        case BUTTON_TRY
            clear cam;
        otherwise
            clear cam;
            fprintf('Camera selection cancelled.\n');
            isQuit = true;
            return;
    end
end

%% Step 3: Enter sampling rate
confirmRate = false;
defaultRate = '30';
while ~confirmRate
    answerRate = inputdlg(...
        'Enter camera sampling rate (Hz):',...
        'Sampling Rate', ...
        1, ...
        {defaultRate});
    if isempty(answerRate)
        fprintf('Sampling rate entry cancelled.\n');
        isQuit = true;
        clear cam;
        return;
    end
    rate_raw = answerRate{1};
    rate = str2double(rate_raw);
    if isnan(rate) || rate<=0
        errdlg = errordlg( ...
            'Please enter a positive number.', ...
            'Invalid Rate', ...
            'modal');
        uiwait(errdlg);
        continue;
    end
    defaultRate = rate_raw;
    quest = sprintf('Sampling rate (Hz): %.1f',rate);
    choice = questdlg(quest,...
        'Confirm Rate', ...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL, ...
        opts);
    switch choice
        case BUTTON_CONFIRM
            confirmRate = true;
        case BUTTON_TRY     % retry
        otherwise
            fprintf('Sampling rate selection cancelled.\n');
            isQuit = true;
            clear cam;
            return;
    end
end

%% Step 4: Choose recording mode
choice = questdlg(...
    'Record continuously or capture periodically?',...
    'Recording Mode',...
    'Continuous','Periodic',BUTTON_CANCEL, ...
    opts);
switch choice
    case 'Continuous'
        camMode = 'continuous';
    case 'Periodic'
        camMode = 'periodic';
        % ask for interval 
        confirmInt = false;
        defaultInterval  ='5';
        while ~confirmInt
            answerInt = inputdlg(...
                'Enter capture interval (seconds):',...
                'Periodic Interval', ...
                1, ...
                {defaultInterval});
            if isempty(answerInt)
                fprintf('Interval entry cancelled.\n');
                isQuit = true;
                clear cam;
                return;
            end
            interval_raw = answerInt{1};
            interval = str2double(interval_raw);
            if isnan(interval) || interval<=0
                errdlg = errordlg( ...
                    'Please enter a positive number.', ...
                    'Invalid Interval', ...
                    'modal');
                uiwait(errdlg);
                continue;
            end
            defaultInterval = interval_raw;
            quest = sprintf('Interval (s): %.1f s',interval);
            choice2 = questdlg(quest,...
                'Confirm Interval', ...
                BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL, ...
                opts);
            switch choice2
                case BUTTON_CONFIRM
                    confirmInt = true;
                case BUTTON_TRY
                    % retry
                otherwise
                    fprintf('Interval selection cancelled.\n');
                    isQuit = true;
                    clear cam;
                    return;
            end
        end
    otherwise
        fprintf('Recording mode selection cancelled.\n');
        isQuit = true;
        clear cam;
        return;
end

%% Store & finish
File.Camera.Object = cam;
File.Camera.Rate = rate;
File.Camera.Mode = camMode;
fprintf('Camera "%s" enabled at %.1f Hz, mode: %s',camName,rate,camMode);
if strcmpi(camMode,'periodic')
    File.Camera.Interval = interval;
    fprintf(' (interval = %.1f s)\n',interval);
else
    fprintf('\n');
end

end
