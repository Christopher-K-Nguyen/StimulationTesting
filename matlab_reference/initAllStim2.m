function [File,isQuit] = initAllStim2(File)
%% Constants
NIL_LIST = {'PLX00078','PLX00089','PLX00161'};
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
Stimulator = struct(...
    'SerialNumber','',...
    'Firmware','',...
    'Description','',...
    'VoltageScaling',0,...  % V/V
    'CurrentScaling',0,...  % mV/uA
    'DigitalDelay',1.5);    % us
errInitAllStim = 1;                       	% initialize errors
isQuit = false;
pauseTime = 6;

%% Function
while errInitAllStim ~= 0                   % get stimulator(s) initialized
    fprintf('Initializing stimulator(s)...');	% initializing stimulator(s)
    startTime = tic;
    PS_CloseAllStim();              % already initialized cause errors
    pause(pauseTime);
    errInitAllStim = PS_InitAllStim();      % get errors
    switch errInitAllStim                       % getting errors
        case 0                                  % no errors
            [serialNum,~] = PS_GetSerialNumber(1);
            if any(strcmpi(serialNum,NIL_LIST))
                voltageScaling = 1;     % V/V
                currentScaling = 1e-3;  % mV/uA
            else
                voltageScaling = 0.25;  % V/V
                currentScaling = 2.5;   % mV/uA
            end
            [firmwareVer,~] = PS_GetFwVersion(1);
            [description,~] = PS_GetDescription(1);
            Stimulator.SerialNumber = serialNum;
            Stimulator.Firmware = firmwareVer;
            Stimulator.Description = description;
            Stimulator.VoltageScaling = voltageScaling;
            Stimulator.CurrentScaling = currentScaling;
            File.Stimulator = Stimulator;
            [endTime,unit] = getEndTime(startTime);
            fprintf('OK (%.3f %s)\n',endTime,unit);   % stimulator(s) initialized
        case 1                                                          % initializing error
            prompt1 = 'ERROR INITIALIZING STIMULATOR(S)';	% stimulator(s) not initialized
            prompt2 = 'Turn the PlexStim off then on.';
            prompt = {prompt1,prompt2};
            fprintf('\n%s',prompt1);
            quest = questdlg(prompt,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        case 2                                                          % no stimulator(s)
            msg = 'ERROR INITIALIZING STIMULATOR(S)';                    % stimulator(s) not initialized
            fprintf('\n%s',msg);
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errInitAllStim ~= 0
        switch quest                        % apply choice
            case BUTTON_QUIT               	% quit
                fprintf('\nQuitting...');	% quitting
                isQuit = true;
                return;                     % exit program
            case BUTTON_TRY                     % try again
                fprintf('\nTrying again...\n\n'); % trying again
                pause(pauseTime);
                % PS_CloseAllStim();              % already initialized cause errors
                % pause(pauseTime);
            otherwise                       % cancel
                fprintf('\nQuitting...');	% quitting
                isQuit = true;
                return;                     % exit program
        end
    end
end

end