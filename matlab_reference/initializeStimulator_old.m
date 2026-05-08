function [File,isQuit] = initializeStimulator(File)
%% Constants
NIL_LIST = {'PLX00078','PLX00089','PLX00161'};
% Buttons
BUTTON_RESET = 'Reset';
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_QUIT = 'Quit';
TITLE_ERROR = 'ERROR';
% Options
opts.Default = BUTTON_TRY;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
% Stimulation Configuration
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');

% Initialization
errInitAllStim = 1;      % initialize errors
isQuit = false; %#ok<NASGU>
pauseTime = 5;
serialNum = '';
firmwareVer = '';
description = '';
voltageScaling = 1;
currentScaling = 1;
isOn = false;
dischargeMode = 1;
isInterpulseLow = logical(dischargeMode);

%% Function
% Find stimulators
while errInitAllStim ~= 0   % get stimulator(s) initialized
    isOn = File.Stimulator.Status;
    if isOn
        if isMP
            break;
        end
    else
        fprintf('Initializing stimulator(s)...');	% initializing stimulator(s)
        startTime = tic;
        errCloseAllStim = PS_CloseAllStim;              % already initialized cause errors
        if errCloseAllStim == 0
            pause(pauseTime);
        end
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
                % Stimulator Info
                [firmwareVer,~] = PS_GetFwVersion(1);
                [description,~] = PS_GetDescription(1);
                isOn = true;
                
                % Discharge Mode
                PS_SetAutoDischarge(1,dischargeMode);
                [dischargeMode_check,~] = PS_GetAutoDischarge(1);
                isInterpulseLow = logical(dischargeMode_check);

                [endTime,unit] = getEndTime(startTime);
                fprintf('OK (%.3f %s)\n',endTime,unit);   % stimulator(s) initialized
            case 1                                                          % initializing error
                prompt1 = 'ERROR INITIALIZING STIMULATOR(S)';	% stimulator(s) not initialized
                prompt2 = 'Turn the PlexStim off then on.';
                prompt = {prompt1,prompt2};
                fprintf('\n%s',prompt1);
                quest = questdlg(prompt,TITLE_ERROR,BUTTON_RESET,BUTTON_TRY,BUTTON_QUIT,opts);
            case 2                                                          % no stimulator(s)
                msg = 'ERROR INITIALIZING STIMULATOR(S)';                    % stimulator(s) not initialized
                fprintf('\n%s',msg);
                quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        end
        if errInitAllStim ~= 0
            switch quest                        % apply choice
                case BUTTON_RESET
                    try
                        pause(pauseTime/2);
                        errCloseAllStim = PS_CloseAllStim;              % already initialized cause errors
                        if errCloseAllStim == 0
                            pause(pauseTime);
                        end
                    catch
                        pause(pauseTime);
                    end
                    case BUTTON_TRY                     % try again
                    fprintf('\nTrying again...\n\n'); % trying again
                case BUTTON_QUIT               	% quit
                    fprintf('\nQuitting...');	% quitting
                    isQuit = true;
                    break;                     % exit program
                otherwise                       % cancel
                    fprintf('\nQuitting...');	% quitting
                    isQuit = true;
                    break;                     % exit program
            end
        end
    end
end
if isQuit
    fprintf('\nQuitting...');	% quitting
    return;
end

% Find stimulators
[numOfStim,isQuit] = getNumOfStim();
if isQuit
    return;
end
if numOfStim == 1
    % Find channels available per stimulators (should be 16, value not used)
    [~,isQuit] = getChannelsFromStim(numOfStim);
    if isQuit
        fprintf('\nQuitting...');	% quitting
        return;
    end
else
    fprintf('NO STIMULATORS CONNECTED!\n\n');
    isQuit = true;
    fprintf('\nQuitting...');	% quitting
    return;
end
%     fprintf('\n');

%% Store
File.Stimulator.SerialNumber = serialNum;
File.Stimulator.Firmware = firmwareVer;
File.Stimulator.Description = description;
File.Stimulator.VoltageScaling = voltageScaling;
File.Stimulator.CurrentScaling = currentScaling;
File.Stimulator.Discharge = isInterpulseLow; %#ok<*LOGL>
File.Stimulator.Status = isOn;

end