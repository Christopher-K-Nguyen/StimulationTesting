function [File,isQuit] = getTest(File)
%% Constants
% Buttons
BUTTON_MAX = 'Maximum';
BUTTON_CHARGE = 'Charge/Phase';
BUTTON_INJECTION = 'Charge Injection';
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS_DIALOG = [1 90];       % dialog dimensions
% Options
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
isQuit = false;
expType = File.Test.Experiment;
isBiphasic = contains2(expType,'VT');
isTriphasic = contains2(expType,'TV');
isVT = isBiphasic || isTriphasic;
isPS = contains2(expType,'PS');
testType = '';
chargePhase = [];
chargeInjection = [];
stepSize = [];
amplitude1 = File.Parameters.Amplitude1;
amplitude2 = File.Parameters.Amplitude2;
phaseWidth1 = File.Parameters.PhaseWidth1;
phaseWidth2 = File.Parameters.PhaseWidth2;
if isTriphasic
    amplitude3 = File.Parameters.Amplitude3;
    phaseWidth3 = File.Parameters.PhaseWidth3;
else
    amplitude3 = 0;
    phaseWidth3 = 0;
end
amplitude_arr = abs([min(amplitude1) min(amplitude2) min(amplitude3)]);
phaseWidth_arr = [phaseWidth1 phaseWidth2 phaseWidth3];
[amplitude,max_idx] = max(amplitude_arr);
phaseWidth = phaseWidth_arr(max_idx);
% polarity = File.Parameters.Polarity;
surfaceArea = File.Parameters.SurfaceArea;
[startChargePhase,startChargeInjection] = getCharge(amplitude,phaseWidth,surfaceArea);

%% Function
if isVT || isPS
    numOfChannels = File.Parameters.NumberOfChannels;
    confirmStimTarget = false;
    while ~confirmStimTarget
        fprintf('Enter stimulation target...');
        opts.Default = BUTTON_MAX;       % option dedault
        questStimTarget = questdlg('Select stimulation target.', ...
            'Stimulation Target', ...
            BUTTON_MAX,BUTTON_CHARGE,BUTTON_INJECTION, ...
            opts);

        promptQuestStimTarget = sprintf('Stimulation Target: {\\bf%s}',questStimTarget);
        % Question box
        opts.Default = BUTTON_CONFIRM;
        questTestParam = questdlg(...                   % question dialog
            promptQuestStimTarget,...                    % question prompts
            'Confirm Stimulation Target',...                     % question title
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
            opts);                                      % dialog options
        % Confirmation
        switch questTestParam                       % apply choice
            case BUTTON_CONFIRM                     % check confirmation
                switch questStimTarget
                    case BUTTON_MAX
                        testType = 'max';
                    case BUTTON_CHARGE
                        testType = 'phase';
                    case BUTTON_INJECTION
                        testType = 'injection';
                end
                confirmStimTarget = true;               % confirm parameters
                fprintf('OK\n');% parameters confirmed
            case BUTTON_TRY                         % try again
                confirmStimTarget = false;               % trying again
                fprintf('Trying again...\n');     % starting over
            case BUTTON_CANCEL                      % quit
                fprintf('Quitting...\n\n');         % quitting
                isQuit = true;
                break;                             % exit program
            otherwise                               % cancel
                fprintf('Quitting...\n\n');         % quitting
                isQuit = true;
                breka;                             % exit program
        end
    end
    if isQuit
        return;
    end

    if ~contains2(testType,'max')
        if ~isscalar(surfaceArea)
            startChargeInjection = 0.2;
        end
        switch testType
            case 'phase'
                target_use = 'Target Charge/phase (nC/ph):';
                defaultTarget = num2str(startChargePhase);
            case 'injection'
                target_use = 'Target Charge/phase (mC/cm^{2})}:';
                defaultTarget = num2str(startChargeInjection);
        end
    else
        target_use = 'Targe maximum charge';
        defaultStepSize = '0';
        chargeInjection = Inf;
        chargePhase = Inf;
        defaultTarget = 'Inf';
    end
    opts.Default = BUTTON_CONFIRM;       % option dedault
    confirmTestParam = false;
    while ~confirmTestParam
        fprintf('Enter test parameters...');
        % Dialog box
        if contains2(testType,'max')
            promptInputTestParam = {'Step size (\muA) {\bf(min 0.1, 0 for no fixed step size)}:'};  % step size (uA)
            defaultInputTestParam = {defaultStepSize};           % input defaults
        else
            promptInputTestParam = {...           	% input prompts
                target_use,...            % limit
                'Step size (\muA) {\bf(min 0.1)}:'};  % step size (uA)
            defaultInputTestParam = {defaultTarget,defaultStepSize};           % input defaults
        end
        inputTestParam = inputdlg(...   % input dialog
            promptInputTestParam,...    % input prompts
            'Test Parameters',...     % input title
            DIMS_DIALOG,...             % dialog dimensions
            defaultInputTestParam,...   % input defaults
            opts);                      % dialog options

        % Collect input
        if isempty(inputTestParam)  % cancel dialog
            fprintf('Quitting...\n\n');             % quitting
            isQuit = true;
            break;                                 % exit program
        end
        if contains2(testType,'max')
            defaultStepSize =  inputTestParam{1};
            stepSize = str2double(defaultStepSize);
        else
            defaultTarget = inputTestParam{1};             % current limit (cell)
            target = str2double(defaultTarget);
            defaultStepSize =  inputTestParam{2};
            stepSize = str2double(defaultStepSize);
        end


        switch testType
            case 'phase'
                chargePhase = target;
            case 'injection'
                chargeInjection = target;
        end
        if all(chargePhase == startChargePhase)
            stepSize = 0;
        end

        % Confirm test parameters
        % Format questions
        stepSize_use = sprintf('Step size: {\\bf%.2f \\muA}',stepSize);  % formatted step size
        if contains2(testType,'max')
            promptQuestTestParam = {stepSize_use};              % inputted step size
        else
            switch testType
                case 'phase'
                    if isscalar(surfaceArea)
                        chargeInjection = chargePhase * 1e2 / surfaceArea;
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf%g nC/ph,  %g mC/cm^{2}}', ...
                            chargePhase,chargeInjection);      % formatted charge
                    else
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf%g nC/ph}', ...
                            chargePhase);      % formatted charge
                    end
                case 'injection'
                    if isscalar(surfaceArea)
                        chargePhase = min(chargeInjection .* surfaceArea * 1e-2);
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf%g mC/cm^{2},  %g nC/ph}', ...
                            chargeInjection,chargePhase);      % formatted charge
                    else
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf%g mC/cm^{2}}', ...
                            chargeInjection);      % formatted charge
                    end
            end
            promptQuestTestParam = {...     % question prompts
                promptTarget_use,...   % inputted current limit
                stepSize_use};              % inputted step size
        end

        % Question box
        questTestParam = questdlg(...                   % question dialog
            promptQuestTestParam,...                    % question prompts
            'Confirm Stimulation Parameters',...                     % question title
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
            opts);                                      % dialog options
        % Confirmation
        switch questTestParam                       % apply choice
            case BUTTON_CONFIRM                     % check confirmation
                confirmTestParam = true;               % confirm parameters
                fprintf('OK\n');% parameters confirmed
            case BUTTON_TRY                         % try again
                confirmTestParam = false;               % trying again
                fprintf('Trying again...\n');     % starting over
            case BUTTON_CANCEL                      % quit
                fprintf('Quitting...\n\n');         % quitting
                isQuit = true;
                break;                             % exit program
            otherwise                               % cancel
                fprintf('Quitting...\n\n');         % quitting
                isQuit = true;
                break;                             % exit program
        end
    end
    if isQuit
        return;
    end
end

%% Store
File.Test.ID = testType;
File.Test.ChargePhase = chargePhase;
File.Test.ChargeInjection = chargeInjection;
File.Test.StepSize = stepSize;

end