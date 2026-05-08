function [charge,chargeName,quitProgram] = selectCharge()

%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

fprintf('Enter charge/phase...');
targetCharge_raw = '4';
confirmCharge = false;
while ~confirmCharge
    promptCharge = 'Charge/Phase (\bfnC/ph)';
    inputCharge = inputdlg(...
        promptCharge,...
        'Charge/Phase',...
        [1 35],...
        {targetCharge_raw},...
        opts);
    targetCharge_raw = inputCharge{1};
    charge = str2double(targetCharge_raw);

    choice = sprintf('Charge/Phase: {\\bf%g nC/ph}',charge);
    questChoice = questdlg(...
        choice,...
        'Charge/Phase',...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
        opts);

    % Confirmation
    switch questChoice                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmCharge = true;               % confirm info
            chargeName = sprintf('%03dnC',charge);
            fprintf('%g nC/ph\n',charge);
            %             fprintf('OK\n');  % info confirmed
        case BUTTON_TRY                             % try again
            confirmCharge = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
    end
end

end