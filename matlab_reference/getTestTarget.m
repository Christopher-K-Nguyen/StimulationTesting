function [File,isQuit] = getTestTarget(File)
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
testType = '';
expID = File.Test.Experiment;
isVT = contains2(expID,'VT');
isPS = contains2(expID,'PS');
% numOfChannels = File.Parameters.NumberOfChannels;
chargePhase = [];
chargeInjection = [];

%% Function
confirmStimTarget = false;
while ~confirmStimTarget && isVT
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

if ~contains2(testType,'max') || isPS
    if isPS
        amplitude1 = 1e3;
    else
        amplitude1 = File.Parameters.Amplitude1;
    end
    phaseWidth1 = File.Parameters.PhaseWidth1;
    phaseWidth2 = File.Parameters.PhaseWidth2;
    % polarity = File.Parameters.Polarity;
    startingChargePhase = min(abs(amplitude1) * phaseWidth1 / 1e3);
    surfaceArea = File.Parameters.SurfaceArea;
    if isscalar(surfaceArea)
        startChargeInjection = min(startingChargePhase * 1e2 / surfaceArea);
    else
        startChargeInjection = 0.2;
    end
    switch testType
        case 'phase'
            if isPS
                target_use = 'Target Charge/phase (nC/ph) {\\bf(Enter Inf for up to limit or 0 for up to failure)}:';
            else
                target_use = 'Target Charge/phase (nC/ph):';
            end
            defaultTarget = num2str(startingChargePhase);
        case 'injection'
            if isPS
                target_use = 'Target Charge/phase (mC/cm^{2}) {\\bf(Enter Inf for up to limit or 0 for up to failure)}:';
            else
                target_use = 'Target Charge/phase (mC/cm^{2}):';
            end
            defaultTarget = num2str(startChargeInjection);
    end
    if isPS
        defaultStepSize = '1';
        defaultTimeStep = '500';
    else
        if all(surfaceArea >= 5000)
            defaultStepSize = '20';
        elseif all(surfaceArea >= 2000)
            defaultStepSize = '10';
        elseif all(surfaceArea >= 1000)
            defaultStepSize = '5';
        elseif all(surfaceArea >= 500)
            defaultStepSize = '2';
        elseif all(surfaceArea >= 200)
            defaultStepSize = '1';
        elseif all(surfaceArea >= 100)
            defaultStepSize = '0.5';
        else
            defaultStepSize = '0.1';
        end
    end
    opts.Default = BUTTON_CONFIRM;       % option dedault
    confirmTestParam = false;
    while ~confirmTestParam
        fprintf('Enter test parameters...');
        % Dialog box
        if isPS
            promptInputTestParam = {...           	% input prompts
                target_use,...            % limit
                'Current step size (\muA) {\bf(min 0.1)}:' % step size (uA)
                'Time size (s):'};  
            defaultInputTestParam = {defaultTarget,defaultStepSize,defaultTimeStep};           % input defaults
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
        defaultTarget = inputTestParam{1};             % current limit (cell)
        target = str2double(defaultTarget);
        defaultStepSize =  inputTestParam{2};
        stepSize = str2double(defaultStepSize);
        if isPS
            defaultTimeStep = inputTestParam{3};
            timeStep = str2double(defaultTimeStep);
        end
        switch testType
            case 'phase'
                chargePhase = target;
            case 'injection'
                chargeInjection = target;
        end
        if all(chargePhase == startingChargePhase)
            stepSize = 0;
        end

        % Confirm test parameters
        % Format questions
        switch testType
            case 'phase'
                if isscalar(surfaceArea)
                    chargeInjection = chargePhase * 100 / surfaceArea;
                    if isinf(chargePhase)
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf\\infty nC/ph,  \\infty mC/cm^{2}}'); % formatted charge
                    elseif chargePhase == 0
                        promptTarget_use = sprintf( ...
                            'Target: {\\bfFAILURE}'); % formatted charge
                    else
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf%g nC/ph,  %g mC/cm^{2}}', ...
                            chargePhase,chargeInjection);      % formatted charge
                    end
                else
                    if isinf(chargePhase)
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf\\infty nC/ph'); % formatted charge
                    elseif chargePhase == 0
                        promptTarget_use = sprintf( ...
                            'Target: {\\bfFAILURE'); % formatted charge
                    else
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf%g nC/ph}', ...
                            chargePhase);      % formatted charge
                    end
                end
                
            case 'injection'
                if isscalar(surfaceArea)
                    chargePhase = min(chargeInjection .* surfaceArea * 1e-2);
                    if isinf(chargeInjection)
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf\\infty mC/cm^{2}},  \\infty nC/ph'); % formatted charge
                    elseif chargeInjection == 0
                        promptTarget_use = sprintf( ...
                            'Target: {\\bfFAILURE'); % formatted charge
                    else
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf%g nC/ph,  %g mC/cm^{2}}', ...
                            chargePhase,chargeInjection);      % formatted charge
                    end
                else
                    if isinf(chargePhase)
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf\\infty mC/cm^{2}}'); % formatted charge
                    elseif chargePhase == 0
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf\FAILURE'); % formatted charge
                    else
                        promptTarget_use = sprintf( ...
                            'Target: {\\bf%g mC/cm^{2}}', ...
                            chargeInjection);      % formatted charge
                    end
                end
        end

        stepSize_use = sprintf('Step size: {\\bf%.2f \\muA}',stepSize);  % formatted step size
        promptQuestTestParam = {...     % question prompts
            promptTarget_use,...   % inputted current limit
            stepSize_use};              % inputted step size
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
else
    chargeInjection = Inf;
    chargePhase = Inf;
    stepSize = 0;
    if isPS
        timeStep = 0;
    end
end
if isQuit
    return;
end

%% Store
File.Test.ID = testType;
File.Test.ChargePhase = chargePhase;
File.Test.ChargeInjection = chargeInjection;
File.Test.StepSize = stepSize;
if isPS
    File.Test.TimeStep = timeStep;
end

end